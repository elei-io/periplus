"""Fixed CDC-driven rebuildable catalogue materializations."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from atlas.materialization.cdc.events import (
    EVENT_STREAM,
    DMLTick,
    dml_subject,
    ensure_cdc_stream,
)
from atlas.platform.config.performance import (
    MATERIALIZATION_QUACK_CLIENTS,
)
from atlas.urls import normalize_url
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from atlas.platform.catalogue import (
    Catalogue,
    ServiceAccountTokenProvider,
)
from atlas.platform.catalogue.duckbasin import DuckBasinConfig
from atlas.platform.catalogue.operations import run_with_catalogue_retry
from atlas.platform.health import HealthMonitor
from atlas.ingestion.objects.config import object_store_from_env
from atlas.ingestion.objects.html import RawHtmlRepository
from atlas.platform.messaging.catalogue_workers import CatalogueLaneReporter
from atlas.platform.messaging.client import connect_nats
from atlas.platform.messaging.leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)

from atlas.materialization.contracts import ProjectionName
from atlas.materialization.document_workload import document_stage_plan
from atlas.materialization.lanes import MaterializationLane, MaterializationLanePool
from atlas.materialization.pipeline import execute_bounded_stage
from atlas.materialization.sql import sql_string_list
from atlas.materialization.visit_workload import (
    changed_visit_rows,
    merge_page_observation_rows,
    merge_page_rows,
    page_observation_row,
    page_row,
    rebuild_page_heads,
)

WorkloadName = Literal["documents", "visits"]
_ACK_WAIT_SECONDS = 90
_FETCH_BATCH = 100
_FETCH_TIMEOUT_SECONDS = 60
_COALESCE_TIMEOUT_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class Workload:
    name: WorkloadName
    source_schema: str
    source_table: str
    stages: tuple[ProjectionName, ...]

    @property
    def durable(self) -> str:
        return f"atlas-material-{self.name}-v1"


def workloads() -> tuple[Workload, ...]:
    return (
        Workload(
            "documents",
            "ingest",
            "documents",
            (
                "content_stats",
                "html_elements",
                "jsonld_values",
                "links",
                "link_occurrences",
            ),
        ),
        Workload(
            "visits",
            "ingest",
            "visits",
            ("pages", "page_observations", "page_heads"),
        ),
    )


async def run(
    *,
    stop: asyncio.Event | None = None,
    monitor: HealthMonitor | None = None,
    lanes: tuple[CatalogueLaneReporter, ...] | None = None,
) -> None:
    stop = stop or asyncio.Event()
    lane_reporters = lanes or tuple(
        CatalogueLaneReporter(lane_index=index)
        for index in range(MATERIALIZATION_QUACK_CLIENTS)
    )
    if len(lane_reporters) != MATERIALIZATION_QUACK_CLIENTS:
        raise ValueError(
            "materialization lane reporters must match configured client lanes"
        )

    client = await connect_nats()
    tokens = ServiceAccountTokenProvider(DuckBasinConfig.from_env())
    materialization_lanes: list[MaterializationLane] = []
    try:
        jetstream = client.jetstream()
        await ensure_cdc_stream(jetstream)
        leases = await ensure_operation_lease_storage(jetstream)
        for index in range(MATERIALIZATION_QUACK_CLIENTS):
            materialization_lanes.append(
                await MaterializationLane.open(index, tokens=tokens)
            )
        lane_pool = MaterializationLanePool(
            materialization_lanes,
            lane_reporters,
        )
        html_repository = RawHtmlRepository(
            object_store_from_env(
                maximum_concurrency=MATERIALIZATION_QUACK_CLIENTS
            )
        )
        for reporter in lane_reporters:
            reporter.attach(lambda: (True, "ready"))
        if monitor is not None:
            monitor.dependencies_ready()
            monitor.subsystem_ready("materialization")

        tasks = [
            asyncio.create_task(
                _run_workload(
                    jetstream,
                    leases,
                    lane_pool,
                    html_repository,
                    workload,
                    stop,
                    monitor,
                ),
                name=f"materialization-{workload.name}",
            )
            for workload in workloads()
        ]
        from atlas.materialization.runtime import run_maintenance

        tasks.append(
            asyncio.create_task(
                run_maintenance(
                    jetstream,
                    leases,
                    lane_pool,
                    html_repository,
                    stop=stop,
                ),
                name="materialization-maintenance",
            )
        )
        await asyncio.gather(*tasks)
    finally:
        await asyncio.gather(
            *(lane.close() for lane in materialization_lanes),
            return_exceptions=True,
        )
        await asyncio.to_thread(tokens.close)
        await client.close()


async def _run_workload(
    jetstream,
    leases,
    lane_pool: MaterializationLanePool,
    html_repository: RawHtmlRepository,
    workload: Workload,
    stop: asyncio.Event,
    monitor: HealthMonitor | None,
) -> None:
    table_uuid = await lane_pool.call(
        _table_uuid,
        workload.source_schema,
        workload.source_table,
    )
    subject = dml_subject(table_uuid)
    subscription = await jetstream.pull_subscribe(
        subject,
        durable=workload.durable,
        stream=EVENT_STREAM,
        config=ConsumerConfig(
            durable_name=workload.durable,
            deliver_policy=DeliverPolicy.ALL,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=_ACK_WAIT_SECONDS,
            max_ack_pending=_FETCH_BATCH,
            filter_subject=subject,
        ),
    )

    consumer_info = await subscription.consumer_info()
    logging.info(
        "resuming %s materialization from durable consumer sequence %s",
        workload.name,
        consumer_info.delivered.consumer_seq,
    )
    while not stop.is_set():
        if monitor is not None:
            monitor.heartbeat()
        try:
            messages = await subscription.fetch(
                batch=_FETCH_BATCH, timeout=_FETCH_TIMEOUT_SECONDS
            )
        except (NatsTimeoutError, TimeoutError):
            continue
        if not messages:
            continue
        while len(messages) < _FETCH_BATCH:
            try:
                additional = await subscription.fetch(
                    batch=_FETCH_BATCH - len(messages),
                    timeout=_COALESCE_TIMEOUT_SECONDS,
                )
            except (NatsTimeoutError, TimeoutError):
                break
            if not additional:
                break
            messages.extend(additional)
        await _process_tick_batch(
            leases,
            workload,
            lane_pool,
            html_repository,
            messages,
        )


async def _process_tick_batch(
    leases,
    workload: Workload,
    lane_pool: MaterializationLanePool,
    html_repository: RawHtmlRepository,
    messages,
) -> None:
    """Coalesce source ticks into one committed materialization operation."""

    ticks = [DMLTick.model_validate_json(msg.data) for msg in messages]
    try:
        await _with_message_heartbeats(
            messages,
            _refresh_workload(
                leases,
                workload,
                lane_pool,
                html_repository,
                ticks=tuple(ticks),
            ),
        )
    except (OperationLeaseUnavailable, OperationLeaseLost):
        for message in messages:
            await message.nak(delay=1)
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        logging.exception(
            "fixed materialization %s failed through snapshot %s",
            workload.name,
            max(tick.snapshot_id for tick in ticks),
        )
        for message in messages:
            await message.nak()
        return
    for message in messages:
        await message.ack()


async def _with_message_heartbeats(messages, operation) -> None:
    task = asyncio.create_task(operation)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=30)
            if done:
                task.result()
                return
            await asyncio.gather(
                *(message.in_progress() for message in messages),
                return_exceptions=True,
            )
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _refresh_workload(
    leases,
    workload: Workload,
    lane_pool: MaterializationLanePool,
    html_repository: RawHtmlRepository,
    *,
    ticks: tuple[DMLTick, ...],
) -> None:
    if workload.name == "documents":
        await _refresh_documents(
            leases,
            lane_pool,
            html_repository,
            ticks,
        )
        return
    await _refresh_visits(
        leases,
        lane_pool,
        html_repository,
        ticks,
    )


async def _run_stage(
    leases,
    lane_pool: MaterializationLanePool,
    target: ProjectionName,
    operation,
    *args,
):
    async with operation_leases(
        leases,
        (f"material.{target}",),
        phase="materialization",
        acquire_timeout=0,
    ):
        return await lane_pool.call(
            _run_stage_with_retry,
            target,
            operation,
            args,
        )


async def _run_stage_group(
    leases,
    lane_pool: MaterializationLanePool,
    targets: tuple[ProjectionName, ...],
    operation,
    *args,
):
    async with operation_leases(
        leases,
        tuple(f"material.{target}" for target in targets),
        phase="materialization",
        acquire_timeout=0,
    ):
        return await lane_pool.call(
            _run_stage_with_retry,
            targets[0],
            operation,
            args,
        )


def _run_stage_with_retry(
    catalogue: Catalogue,
    target: ProjectionName,
    operation,
    args: tuple,
):
    return run_with_catalogue_retry(
        lambda: operation(catalogue, *args),
        description=f"materialization stage {target}",
    )


async def _refresh_documents(
    leases,
    lane_pool: MaterializationLanePool,
    html_repository: RawHtmlRepository,
    ticks: tuple[DMLTick, ...],
) -> None:
    start_snapshot, end_snapshot = _snapshot_window(ticks)
    result = await execute_bounded_stage(
        leases,
        lane_pool,
        document_stage_plan(html_repository, lane_pool.capacity),
        start_snapshot,
        end_snapshot,
    )
    logging.info(
        "incremental document workload snapshots %s-%s "
        "source_items=%s source_bytes=%s source_partitions=%s "
        "write_partitions=%s rows=%s "
        "projection_bytes=%s select=%.3fs project=%.3fs "
        "write=%.3fs elapsed=%.3fs",
        start_snapshot,
        end_snapshot,
        result.source_items,
        result.source_bytes,
        result.partitions,
        result.write_partitions,
        result.output_rows,
        result.output_bytes,
        result.select_seconds,
        result.project_seconds,
        result.write_seconds,
        result.elapsed_seconds,
    )


async def _refresh_visits(
    leases,
    lane_pool: MaterializationLanePool,
    html_repository: RawHtmlRepository,
    ticks: tuple[DMLTick, ...],
) -> None:
    await _gather_stages(
        _run_stage(
            leases,
            lane_pool,
            "pages",
            _refresh_pages_incremental,
            html_repository,
            ticks,
        ),
        _run_stage_group(
            leases,
            lane_pool,
            ("page_observations", "page_heads"),
            _refresh_page_observations_and_heads_incremental,
            html_repository,
            ticks,
        ),
    )


async def _gather_stages(*operations):
    results = await asyncio.gather(*operations, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


def _table_uuid(
    catalogue: Catalogue, schema_name: str, table_name: str
) -> UUID:
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT lake_table.table_uuid
        FROM ducklake_table_info('{catalogue.config.alias}') AS lake_table
        JOIN information_schema.tables AS names
          ON names.table_name = lake_table.table_name
        WHERE names.table_catalog = '{catalogue.config.alias}'
          AND names.table_schema = '{schema_name}'
          AND names.table_name = '{table_name}'
        """
    )
    if len(rows) != 1:
        raise RuntimeError(
            f"expected exactly one source table {schema_name}.{table_name}"
        )
    return UUID(str(rows[0][0]))


def _refresh_pages_incremental(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    ticks: tuple[DMLTick, ...],
) -> None:
    start_snapshot, end_snapshot = _snapshot_window(ticks)
    _visit_ids, changed = changed_visit_rows(
        catalogue,
        start_snapshot=start_snapshot,
        end_snapshot=end_snapshot,
    )
    normalized_urls = {
        normalize_url(str(raw_url))
        for _visit_id, _document_id, raw_url, _observed_at in changed
        if raw_url is not None
    }
    rows = [page_row(url) for url in sorted(normalized_urls)]
    merge_page_rows(catalogue, rows)
    logging.info(
        "incremental pages snapshots %s-%s affected_urls=%s inserted_candidates=%s",
        start_snapshot,
        end_snapshot,
        len(normalized_urls),
        len(rows),
    )


def _refresh_page_observations_and_heads_incremental(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    ticks: tuple[DMLTick, ...],
) -> None:
    start_snapshot, end_snapshot = _snapshot_window(ticks)
    changed_visit_ids, changed = changed_visit_rows(
        catalogue,
        start_snapshot=start_snapshot,
        end_snapshot=end_snapshot,
    )
    rows_by_identity: dict[str, dict[str, object]] = {}
    old_page_ids = {
        str(page_id)
        for (page_id,) in catalogue.trusted_remote_rows(
            "SELECT DISTINCT page_id::VARCHAR "
            "FROM material.page_observations "
            "WHERE visit_id IN "
            f"({sql_string_list(set(changed_visit_ids))})"
        )
    } if changed_visit_ids else set()
    for values in changed:
        row = page_observation_row(*values)
        rows_by_identity[str(row["visit_id"])] = row
    rows = [
        rows_by_identity[identity]
        for identity in sorted(rows_by_identity)
    ]
    affected_page_ids = old_page_ids | {
        str(row["page_id"]) for row in rows
    }
    merge_page_observation_rows(
        catalogue,
        rows,
        replaced_visit_ids=changed_visit_ids,
    )
    rebuild_page_heads(
        catalogue,
        affected_page_ids,
    )
    logging.info(
        "incremental page evidence snapshots %s-%s "
        "observations=%s affected_page_heads=%s",
        start_snapshot,
        end_snapshot,
        len(rows),
        len(affected_page_ids),
    )


def _snapshot_window(ticks: tuple[DMLTick, ...]) -> tuple[int, int]:
    return (
        min(tick.start_snapshot for tick in ticks),
        max(tick.end_snapshot for tick in ticks),
    )
