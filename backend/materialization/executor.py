"""Fixed CDC-driven rebuildable catalogue materializations."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import pyarrow as pa
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
import tldextract

from config.performance import (
    MATERIALIZATION_QUACK_CLIENTS,
)
from control.urls import normalize_url
from dom import (
    ElementRow,
    iter_html_byte_elements,
    iter_html_elements,
    links_from_elements,
)
from repository.catalogue import (
    Catalogue,
    ServiceAccountTokenProvider,
    link_id_for,
    page_id_for,
)
from repository.catalogue.duckbasin import DuckBasinConfig
from repository.catalogue.operations import run_with_catalogue_retry
from repository.objects.config import object_store_from_env
from repository.objects.document import ExactDocumentRepository
from repository.objects.html import RawHtmlRepository
from repository.ingestion.health import HealthMonitor
from cdc.events import (
    DMLTick,
    EVENT_STREAM,
    dml_subject,
    ensure_cdc_stream,
)
from runtime.nats_client import connect_nats
from runtime.catalogue_workers import CatalogueLaneReporter
from materialization.lanes import MaterializationLane, MaterializationLanePool
from materialization.pipeline import (
    BoundedStagePlan,
    StageExecution,
    StageSelection,
    execute_bounded_stage,
)
from runtime.operation_leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)


ProjectionName = Literal[
    "html_elements",
    "jsonld_values",
    "pages",
    "page_observations",
    "links",
    "link_observations",
]
WorkloadName = Literal["documents", "visits"]
_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())
_ACK_WAIT_SECONDS = 90
_FETCH_BATCH = 100
_FETCH_TIMEOUT_SECONDS = 60
_COALESCE_TIMEOUT_SECONDS = 0.1
_MERGE_BATCH = 250
_HTML_PARTITION_ITEMS = 24
_HTML_PARTITION_BYTES = 16 * 1024 * 1024
_LINK_PARTITION_ITEMS = 32
_LINK_PARTITION_BYTES = 16 * 1024 * 1024
_SQL_ID_BATCH = 500


@dataclass(frozen=True, slots=True)
class Workload:
    name: WorkloadName
    source_schema: str
    source_table: str
    stages: tuple[ProjectionName, ...]

    @property
    def durable(self) -> str:
        return f"atlas-material-{self.name}-v1"


@dataclass(frozen=True, slots=True)
class HtmlStageSource:
    content_sha256: str
    object_key: str
    storage_encoding: str
    content_bytes: int


@dataclass(frozen=True, slots=True)
class HtmlStageOutput:
    rows: list[dict[str, object]]
    removed_hashes: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class LinkStageSource:
    document_id: str
    content_sha256: str | None
    source_url: str | None
    observed_at: datetime | None
    content_bytes: int


@dataclass(frozen=True, slots=True)
class LinkStageOutput:
    document_ids: frozenset[str]
    link_rows: list[dict[str, object]]
    observation_rows: list[dict[str, object]]


class MaterializationDependencyNotReady(RuntimeError):
    """A source delta is committed but its derived dependency is not."""


def workloads() -> tuple[Workload, ...]:
    return (
        Workload(
            "documents",
            "ingest",
            "documents",
            (
                "html_elements",
                "jsonld_values",
                "links",
                "link_observations",
            ),
        ),
        Workload(
            "visits",
            "ingest",
            "visits",
            ("pages", "page_observations"),
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
        from materialization.runtime import run_maintenance

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
    except MaterializationDependencyNotReady as exc:
        logging.info("%s; retrying materialization batch", exc)
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
    html_result = await execute_bounded_stage(
        leases,
        lane_pool,
        _html_stage_plan(html_repository, lane_pool.capacity),
        start_snapshot,
        end_snapshot,
    )
    affected_hashes = await lane_pool.call(
        _changed_html_hash_range,
        start_snapshot,
        end_snapshot,
    )
    jsonld_rows, link_rows = await _gather_stages(
        _run_stage(
            leases,
            lane_pool,
            "jsonld_values",
            _materialize_jsonld_hashes,
            affected_hashes,
        ),
        execute_bounded_stage(
            leases,
            lane_pool,
            _link_stage_plan(lane_pool.capacity),
            start_snapshot,
            end_snapshot,
        ),
    )
    logging.info(
        "incremental document workload snapshots %s-%s "
        "affected_hashes=%s projected_hashes=%s html_partitions=%s "
        "jsonld_rows=%s link_rows=%s",
        start_snapshot,
        end_snapshot,
        len(affected_hashes),
        html_result.source_items,
        html_result.partitions,
        jsonld_rows,
        link_rows.output_rows,
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
        _run_stage(
            leases,
            lane_pool,
            "page_observations",
            _refresh_page_observations_incremental,
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


def _html_documents(
    catalogue: Catalogue,
    content_hashes: set[str] | None = None,
) -> list[tuple[str, str, str]]:
    hash_filter = ""
    if content_hashes is not None:
        if not content_hashes:
            return []
        hash_filter = (
            "\n          AND content_sha256 IN "
            f"({_sql_string_list(content_hashes)})"
        )
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT content_sha256, object_key, storage_encoding
        FROM ingest.documents
        WHERE lower(detected_media_type) = 'text/html'
        {hash_filter}
        ORDER BY
          content_sha256,
          CASE storage_encoding WHEN 'zstd' THEN 0 ELSE 1 END,
          object_key
        """
    )
    documents: dict[str, tuple[str, str, str]] = {}
    for content_hash, object_key, storage_encoding in rows:
        value = str(content_hash)
        documents.setdefault(
            value,
            (
                value,
                str(object_key),
                str(storage_encoding),
            ),
        )
    return list(documents.values())


def _project_html_documents(
    html_repository: RawHtmlRepository,
    documents: list[tuple[str, str, str]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    exact_repository = ExactDocumentRepository(html_repository.store)
    for (
        content_sha256,
        object_key,
        storage_encoding,
    ) in documents:
        if storage_encoding == "zstd":
            html: str | bytes = html_repository.read(object_key)
        elif storage_encoding == "identity":
            html = exact_repository.read_bytes(object_key)
        else:
            raise ValueError(
                f"unsupported HTML storage encoding {storage_encoding!r}"
            )
        elements = (
            iter_html_byte_elements(html)
            if isinstance(html, bytes)
            else iter_html_elements(html)
        )
        for element in elements:
            rows.append(
                {
                    "content_sha256": content_sha256,
                    "element_index": element.element_index,
                    "parent_index": element.parent_index,
                    "subtree_end_index": element.subtree_end_index,
                    "depth": element.depth,
                    "child_index": element.child_index,
                    "tag": element.tag.lower(),
                    "namespace": _namespace_name(element.namespace_uri),
                    "attributes": element.attributes,
                    "text_direct": element.text_direct,
                    "text_tail": element.text_tail,
                }
            )
    return rows


def _html_stage_plan(
    html_repository: RawHtmlRepository,
    parallelism: int,
) -> BoundedStagePlan[HtmlStageSource, HtmlStageOutput]:
    return BoundedStagePlan(
        name="html_elements",
        target="html_elements",
        select=_select_html_stage,
        project=lambda catalogue, sources: _project_html_stage(
            catalogue,
            sources,
            html_repository,
        ),
        write=_write_html_stage,
        source_bytes=lambda source: source.content_bytes,
        item_budget=_HTML_PARTITION_ITEMS,
        byte_budget=_HTML_PARTITION_BYTES,
        parallelism=parallelism,
    )


def _select_html_stage(
    catalogue: Catalogue,
    start_snapshot: int,
    end_snapshot: int,
) -> StageSelection[HtmlStageSource, HtmlStageOutput]:
    affected_hashes = _changed_html_hashes(
        catalogue,
        start_snapshot=start_snapshot,
        end_snapshot=end_snapshot,
    )
    if not affected_hashes:
        return StageSelection(items=())
    live_documents = {
        content_hash: (object_key, storage_encoding)
        for content_hash, object_key, storage_encoding in _html_documents(
            catalogue,
            affected_hashes,
        )
    }
    sizes = {
        str(content_hash): int(content_bytes)
        for content_hash, content_bytes in catalogue.trusted_remote_rows(
            f"""
            SELECT content_sha256, max(content_bytes)
            FROM ingest.documents
            WHERE content_sha256 IN (
              {_sql_string_list(affected_hashes)}
            )
            GROUP BY content_sha256
            """
        )
    }
    covered_hashes = _covered_html_hashes(catalogue, affected_hashes)
    missing_hashes = live_documents.keys() - covered_hashes
    removed_hashes = covered_hashes - live_documents.keys()
    items = tuple(
        HtmlStageSource(
            content_sha256=content_hash,
            object_key=live_documents[content_hash][0],
            storage_encoding=live_documents[content_hash][1],
            content_bytes=sizes.get(content_hash, 0),
        )
        for content_hash in sorted(missing_hashes)
    )
    initial_outputs = (
        (
            HtmlStageOutput(
                rows=[],
                removed_hashes=frozenset(removed_hashes),
            ),
        )
        if removed_hashes
        else ()
    )
    return StageSelection(
        items=items,
        initial_outputs=initial_outputs,
    )


def _project_html_stage(
    catalogue: Catalogue,
    sources: tuple[HtmlStageSource, ...],
    html_repository: RawHtmlRepository,
) -> HtmlStageOutput:
    del catalogue
    rows = _project_html_documents(
        html_repository,
        [
            (
                source.content_sha256,
                source.object_key,
                source.storage_encoding,
            )
            for source in sources
        ],
    )
    return HtmlStageOutput(rows=rows)


def _write_html_stage(
    catalogue: Catalogue,
    output: HtmlStageOutput,
) -> int:
    _apply_html_element_delta(
        catalogue,
        rows=output.rows,
        removed_hashes=set(output.removed_hashes),
    )
    return len(output.rows)


def _changed_html_hash_range(
    catalogue: Catalogue,
    start_snapshot: int,
    end_snapshot: int,
) -> set[str]:
    return _changed_html_hashes(
        catalogue,
        start_snapshot=start_snapshot,
        end_snapshot=end_snapshot,
    )


def _changed_html_hashes(
    catalogue: Catalogue,
    *,
    start_snapshot: int,
    end_snapshot: int,
) -> set[str]:
    alias = _sql_string(catalogue.config.alias)
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT DISTINCT content_sha256
        FROM ducklake_table_changes(
          {alias}, 'ingest', 'documents',
          {start_snapshot}, {end_snapshot}
        )
        WHERE lower(detected_media_type) = 'text/html'
          AND content_sha256 IS NOT NULL
        """
    )
    return {str(content_hash) for (content_hash,) in rows}


def _covered_html_hashes(
    catalogue: Catalogue,
    content_hashes: set[str],
) -> set[str]:
    if not content_hashes:
        return set()
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT DISTINCT content_sha256
        FROM material.html_elements
        WHERE content_sha256 IN ({_sql_string_list(content_hashes)})
        """
    )
    return {str(content_hash) for (content_hash,) in rows}


def _apply_html_element_delta(
    catalogue: Catalogue,
    *,
    rows: list[dict[str, object]],
    removed_hashes: set[str],
    table_name: str = "html_elements",
) -> None:
    if not rows and not removed_hashes:
        return
    target = f"material.{table_name}"
    element_registration = "_atlas_material_html_elements_delta"
    with catalogue.remote_transaction():
        if rows:
            catalogue.trusted_connection.register(
                element_registration,
                _arrow_table(rows, map_columns={"attributes"}),
            )
        try:
            if removed_hashes:
                values = ", ".join(
                    f"({_sql_string(content_hash)})"
                    for content_hash in sorted(removed_hashes)
                )
                catalogue.trusted_remote_execute(
                    f"""
                    MERGE INTO {target} AS target
                    USING (VALUES {values}) AS removals(content_sha256)
                      ON target.content_sha256 = removals.content_sha256
                    WHEN MATCHED THEN DELETE
                    """
                )
            if rows:
                catalogue.trusted_connection.execute(
                    f"""
                    INSERT INTO {target} BY NAME
                    SELECT *
                    FROM _atlas_material_html_elements_delta
                    """
                )
        finally:
            if rows:
                catalogue.trusted_connection.unregister(element_registration)


def _materialize_jsonld_hashes(
    catalogue: Catalogue,
    hashes: set[str],
) -> int:
    if not hashes:
        return 0
    source = catalogue.trusted_remote_rows(
        f"""
        SELECT content_sha256, element_index, attributes, text_direct
        FROM material.html_elements
        WHERE content_sha256 IN ({_sql_string_list(hashes)})
          AND tag = 'script'
        ORDER BY content_sha256, element_index
        """
    )
    rows = _jsonld_rows(source)
    _replace_content_hash_slices(
        catalogue,
        table_name="jsonld_values",
        content_hashes=hashes,
        rows=rows,
        variant_columns={"value"},
    )
    return len(rows)


def _jsonld_rows(source) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for content_sha256, element_index, attributes, text in source:
        attrs = dict(attributes or {})
        media_type = str(attrs.get("type") or "").split(";", 1)[0].strip().lower()
        if media_type != "application/ld+json":
            continue
        try:
            value = json.loads(str(text))
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "content_sha256": str(content_sha256),
                "element_index": int(element_index),
                "type_terms": sorted(_jsonld_type_terms(value)),
                "value": json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            }
        )
    return rows


def _refresh_pages_incremental(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    ticks: tuple[DMLTick, ...],
) -> None:
    start_snapshot, end_snapshot = _snapshot_window(ticks)
    alias = _sql_string(catalogue.config.alias)
    changed = catalogue.trusted_remote_rows(
        f"""
        SELECT coalesce(effective_url, requested_url)
        FROM ducklake_table_changes(
          {alias}, 'ingest', 'visits',
          {start_snapshot}, {end_snapshot}
        )
        WHERE observed_at IS NOT NULL
          AND change_type IN ('insert', 'update_postimage')
        """
    )
    normalized_urls = {
        normalize_url(str(raw_url))
        for (raw_url,) in changed
        if raw_url is not None
    }
    rows = [_page_row(url) for url in sorted(normalized_urls)]
    _merge_page_rows(catalogue, rows)
    logging.info(
        "incremental pages snapshots %s-%s affected_urls=%s inserted_candidates=%s",
        start_snapshot,
        end_snapshot,
        len(normalized_urls),
        len(rows),
    )


def _page_row(normalized_url: str) -> dict[str, object]:
    parsed = urlsplit(normalized_url)
    return {
        "page_id": str(page_id_for(normalized_url)),
        "normalized_url": normalized_url,
        "scheme": parsed.scheme,
        "hostname": parsed.hostname,
        "port": parsed.port,
        "path": parsed.path,
        "query": parsed.query or None,
        "registrable_domain": _registrable_domain(parsed.hostname or ""),
    }


def _refresh_page_observations_incremental(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    ticks: tuple[DMLTick, ...],
) -> None:
    start_snapshot, end_snapshot = _snapshot_window(ticks)
    alias = _sql_string(catalogue.config.alias)
    changed = catalogue.trusted_remote_rows(
        f"""
        SELECT visit_id, document_id,
               coalesce(effective_url, requested_url), observed_at
        FROM ducklake_table_changes(
          {alias}, 'ingest', 'visits',
          {start_snapshot}, {end_snapshot}
        )
        WHERE observed_at IS NOT NULL
          AND change_type IN ('insert', 'update_postimage')
        """
    )
    rows_by_identity: dict[tuple[str, str], dict[str, object]] = {}
    for values in changed:
        row = _page_observation_row(*values)
        rows_by_identity[
            (str(row["page_id"]), str(row["visit_id"]))
        ] = row
    rows = [
        rows_by_identity[identity]
        for identity in sorted(rows_by_identity)
    ]
    _merge_page_observation_rows(catalogue, rows)
    logging.info(
        "incremental page_observations snapshots %s-%s observations=%s",
        start_snapshot,
        end_snapshot,
        len(rows),
    )


def _page_observation_row(
    visit_id: object,
    document_id: object,
    raw_url: object,
    observed_at: object,
) -> dict[str, object]:
    normalized_url = normalize_url(str(raw_url))
    return {
        "page_id": str(page_id_for(normalized_url)),
        "visit_id": str(visit_id),
        "document_id": str(document_id) if document_id is not None else None,
        "observed_at": observed_at,
    }


def _link_stage_plan(
    parallelism: int,
) -> BoundedStagePlan[LinkStageSource, LinkStageOutput]:
    return BoundedStagePlan(
        name="links",
        target="links",
        select=_select_link_stage,
        project=_project_link_stage,
        write=_write_link_stage,
        source_bytes=lambda source: source.content_bytes,
        item_budget=_LINK_PARTITION_ITEMS,
        byte_budget=_LINK_PARTITION_BYTES,
        parallelism=parallelism,
        additional_targets=("link_observations",),
    )


def _select_link_stage(
    catalogue: Catalogue,
    start_snapshot: int,
    end_snapshot: int,
) -> StageSelection[LinkStageSource, LinkStageOutput]:
    changed_document_ids = _changed_document_ids(
        catalogue,
        start_snapshot=start_snapshot,
        end_snapshot=end_snapshot,
    )
    rows = _link_source_rows(
        catalogue,
        changed_document_ids,
        snapshot=end_snapshot,
    )
    return StageSelection(
        items=tuple(
            LinkStageSource(
                document_id=str(document_id),
                content_sha256=(
                    str(content_hash)
                    if content_hash is not None
                    else None
                ),
                source_url=(
                    normalize_url(str(source_url))
                    if source_url is not None and observed_at is not None
                    else None
                ),
                observed_at=observed_at,
                content_bytes=int(content_bytes),
            )
            for document_id, content_hash, source_url, observed_at, content_bytes
            in rows
        ),
    )


def _project_link_stage(
    catalogue: Catalogue,
    sources: tuple[LinkStageSource, ...],
) -> LinkStageOutput:
    documents: list[tuple[str, str, str, datetime]] = [
        (
            source.document_id,
            source.content_sha256,
            source.source_url,
            source.observed_at,
        )
        for source in sources
        if source.content_sha256 is not None
        and source.source_url is not None
        and source.observed_at is not None
    ]
    output = _link_rows_for_documents(catalogue, documents)
    return LinkStageOutput(
        document_ids=frozenset(source.document_id for source in sources),
        link_rows=output.link_rows,
        observation_rows=output.observation_rows,
    )


def _write_link_stage(
    catalogue: Catalogue,
    output: LinkStageOutput,
) -> int:
    return _replace_link_document_slices(catalogue, output)


def _link_rows_for_documents(
    catalogue: Catalogue,
    documents: list[tuple[str, str, str, datetime]],
    *,
    elements_table: str = "html_elements",
) -> LinkStageOutput:
    document_ids = frozenset(document_id for document_id, *_ in documents)
    if not documents:
        return LinkStageOutput(
            document_ids=document_ids,
            link_rows=[],
            observation_rows=[],
        )
    hashes = {content_hash for _, content_hash, _, _ in documents}
    elements_by_hash = _elements_by_hash(
        catalogue,
        hashes,
        table_name=elements_table,
    )
    missing = hashes - elements_by_hash.keys()
    if missing:
        examples = ", ".join(sorted(missing)[:3])
        suffix = ", ..." if len(missing) > 3 else ""
        raise MaterializationDependencyNotReady(
            f"HTML projection dependency is not committed for "
            f"{len(missing)} content hashes ({examples}{suffix})"
        )
    link_rows: dict[str, dict[str, object]] = {}
    observation_rows: dict[tuple[str, int], dict[str, object]] = {}
    for document_id, content_hash, source_url, observed_at in documents:
        grouped = links_from_elements(
            elements_by_hash[content_hash],
            page_url=source_url,
        )
        for link in (*grouped["internal"], *grouped["external"]):
            row = _link_row(
                str(link["source_url"]),
                str(link["target_url"]),
            )
            link_id = str(row["link_id"])
            link_rows[link_id] = row
            observation_rows[
                (document_id, int(link["element_index"]))
            ] = {
                "link_id": link_id,
                "document_id": document_id,
                "content_sha256": content_hash,
                "element_index": int(link["element_index"]),
                "raw_href": str(link["raw_href"]),
                "observed_at": observed_at,
            }
    return LinkStageOutput(
        document_ids=document_ids,
        link_rows=[link_rows[key] for key in sorted(link_rows)],
        observation_rows=[
            observation_rows[key] for key in sorted(observation_rows)
        ],
    )


def _link_row(
    source_url: str,
    target_url: str,
) -> dict[str, object]:
    source_page_id = page_id_for(source_url)
    target_page_id = page_id_for(target_url)
    return {
        "link_id": str(link_id_for(source_page_id, target_page_id)),
        "source_page_id": str(source_page_id),
        "target_page_id": str(target_page_id),
        "source_url": source_url,
        "target_url": target_url,
        "relation_scope": _relation_scope(source_url, target_url),
    }


def _changed_document_ids(
    catalogue: Catalogue,
    *,
    start_snapshot: int,
    end_snapshot: int,
) -> list[str]:
    alias = _sql_string(catalogue.config.alias)
    return [
        str(document_id)
        for (document_id,) in catalogue.trusted_remote_rows(
            f"""
            SELECT DISTINCT document_id
            FROM ducklake_table_changes(
              {alias}, 'ingest', 'documents',
              {start_snapshot}, {end_snapshot}
            )
            WHERE document_id IS NOT NULL
            ORDER BY document_id
            """
        )
    ]


def _link_source_rows(
    catalogue: Catalogue,
    document_ids: list[str],
    *,
    snapshot: int,
) -> list[tuple[str, str | None, str | None, datetime | None, int]]:
    if not document_ids:
        return []
    documents: dict[str, tuple[str, str, int]] = {}
    for batch in _value_batches(document_ids, _SQL_ID_BATCH):
        for document_id, visit_id, content_hash, content_bytes in (
            catalogue.trusted_remote_rows(
                f"""
                SELECT document_id::VARCHAR, visit_id::VARCHAR,
                       content_sha256, content_bytes
                FROM ingest.documents AT (VERSION => {snapshot})
                WHERE document_id IN ({_sql_string_list(set(batch))})
                  AND lower(detected_media_type) = 'text/html'
                """
            )
        ):
            documents[str(document_id)] = (
                str(visit_id),
                str(content_hash),
                int(content_bytes),
            )
    visit_ids = sorted({row[0] for row in documents.values()})
    visits: dict[str, tuple[str, datetime]] = {}
    for batch in _value_batches(visit_ids, _SQL_ID_BATCH):
        for visit_id, raw_url, observed_at in catalogue.trusted_remote_rows(
            f"""
            SELECT visit_id::VARCHAR,
                   coalesce(effective_url, requested_url),
                   observed_at
            FROM ingest.visits AT (VERSION => {snapshot})
            WHERE visit_id IN ({_sql_string_list(set(batch))})
              AND observed_at IS NOT NULL
            """
        ):
            visits[str(visit_id)] = (
                normalize_url(str(raw_url)),
                observed_at,
            )
    rows: list[
        tuple[str, str | None, str | None, datetime | None, int]
    ] = []
    for document_id in document_ids:
        document = documents.get(document_id)
        visit = visits.get(document[0]) if document is not None else None
        if document is None or visit is None:
            rows.append((document_id, None, None, None, 0))
            continue
        rows.append(
            (
                document_id,
                document[1],
                visit[0],
                visit[1],
                document[2],
            )
        )
    return rows


def _elements_by_hash(
    catalogue: Catalogue,
    content_hashes: set[str],
    *,
    table_name: str = "html_elements",
) -> dict[str, list[ElementRow]]:
    if not content_hashes:
        return {}
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT content_sha256, element_index, parent_index,
               subtree_end_index, depth, child_index, tag, namespace,
               attributes, text_direct, text_tail
        FROM material.{table_name}
        WHERE content_sha256 IN ({_sql_string_list(content_hashes)})
        ORDER BY content_sha256, element_index
        """
    )
    projected: dict[str, list[ElementRow]] = defaultdict(list)
    for values in rows:
        projected[str(values[0])].append(
            ElementRow(
                element_index=int(values[1]),
                parent_index=(
                    int(values[2]) if values[2] is not None else None
                ),
                subtree_end_index=int(values[3]),
                depth=int(values[4]),
                child_index=int(values[5]),
                tag=str(values[6]),
                namespace_uri=str(values[7]),
                attributes=dict(values[8] or {}),
                text_direct=str(values[9]),
                text_tail=str(values[10]),
            )
        )
    return dict(projected)


def _snapshot_window(ticks: tuple[DMLTick, ...]) -> tuple[int, int]:
    return (
        min(tick.start_snapshot for tick in ticks),
        max(tick.end_snapshot for tick in ticks),
    )


def _replace_content_hash_slices(
    catalogue: Catalogue,
    *,
    table_name: str,
    content_hashes: set[str],
    rows: list[dict[str, object]],
    variant_columns: set[str] = frozenset(),
) -> None:
    if not content_hashes:
        return
    target = f"material.{table_name}"
    registration = f"_atlas_material_{table_name}_delta"
    with catalogue.remote_transaction():
        if rows:
            catalogue.trusted_connection.register(
                registration,
                _arrow_table(rows),
            )
        try:
            values = ", ".join(
                f"({_sql_string(content_hash)})"
                for content_hash in sorted(content_hashes)
            )
            catalogue.trusted_remote_execute(
                f"""
                MERGE INTO {target} AS target
                USING (VALUES {values}) AS changed(content_sha256)
                  ON target.content_sha256 = changed.content_sha256
                WHEN MATCHED THEN DELETE
                """
            )
            if rows:
                names = rows[0].keys()
                projection = ", ".join(
                    f"{name}::JSON::VARIANT AS {name}"
                    if name in variant_columns
                    else name
                    for name in names
                )
                catalogue.trusted_connection.execute(
                    f"INSERT INTO {target} BY NAME "
                    f"SELECT {projection} FROM {registration}"
                )
        finally:
            if rows:
                catalogue.trusted_connection.unregister(registration)


def _merge_page_rows(
    catalogue: Catalogue,
    rows: list[dict[str, object]],
    *,
    table_name: str = "pages",
) -> None:
    if not rows:
        return
    with catalogue.remote_transaction():
        for batch in _row_batches(rows):
            values = ", ".join(
                "("
                + ", ".join(
                    (
                        f"UUID {_sql_string(str(row['page_id']))}",
                        _sql_string(str(row["normalized_url"])),
                        _sql_string(str(row["scheme"])),
                        _sql_string(str(row["hostname"])),
                        _sql_nullable_integer(row["port"]),
                        _sql_string(str(row["path"])),
                        _sql_nullable_string(row["query"]),
                        _sql_nullable_string(row["registrable_domain"]),
                    )
                )
                + ")"
                for row in batch
            )
            catalogue.trusted_remote_execute(
                f"""
                MERGE INTO material.{table_name} AS target
                USING (VALUES {values}) AS delta(
                  page_id, normalized_url, scheme, hostname, port, path, query,
                  registrable_domain
                )
                  ON target.page_id = delta.page_id
                 AND target.normalized_url = delta.normalized_url
                WHEN NOT MATCHED THEN INSERT
                """
            )


def _merge_page_observation_rows(
    catalogue: Catalogue,
    rows: list[dict[str, object]],
    *,
    table_name: str = "page_observations",
) -> None:
    if not rows:
        return
    with catalogue.remote_transaction():
        for batch in _row_batches(rows):
            values = ", ".join(
                "("
                + ", ".join(
                    (
                        f"UUID {_sql_string(str(row['page_id']))}",
                        f"UUID {_sql_string(str(row['visit_id']))}",
                        (
                            "NULL"
                            if row["document_id"] is None
                            else f"UUID {_sql_string(str(row['document_id']))}"
                        ),
                        _sql_timestamp(row["observed_at"]),
                    )
                )
                + ")"
                for row in batch
            )
            catalogue.trusted_remote_execute(
                f"""
                MERGE INTO material.{table_name} AS target
                USING (VALUES {values}) AS delta(
                  page_id, visit_id, document_id, observed_at
                )
                  ON target.page_id = delta.page_id
                 AND target.visit_id = delta.visit_id
                WHEN NOT MATCHED THEN INSERT
                """
            )


def _append_missing_link_rows(
    catalogue: Catalogue,
    rows: list[dict[str, object]],
    *,
    table_name: str = "links",
) -> int:
    if not rows:
        return 0
    source_page_ids = {
        str(row["source_page_id"])
        for row in rows
    }
    existing = {
        str(link_id)
        for (link_id,) in catalogue.trusted_remote_rows(
            f"""
            SELECT link_id::VARCHAR
            FROM material.{table_name}
            WHERE source_page_id IN (
              {_sql_string_list(source_page_ids)}
            )
            """
        )
    }
    missing = [
        row for row in rows
        if str(row["link_id"]) not in existing
    ]
    catalogue.append(table_name, missing, schema_name="material")
    return len(missing)


def _replace_link_observation_rows(
    catalogue: Catalogue,
    *,
    document_ids: frozenset[str],
    rows: list[dict[str, object]],
    table_name: str = "link_observations",
) -> int:
    if not document_ids:
        return 0
    catalogue.trusted_remote_execute(
        f"""
        DELETE FROM material.{table_name}
        WHERE document_id IN ({_sql_string_list(set(document_ids))})
        """
    )
    catalogue.append(table_name, rows, schema_name="material")
    return len(rows)


def _replace_link_document_slices(
    catalogue: Catalogue,
    output: LinkStageOutput,
    *,
    links_table: str = "links",
    observations_table: str = "link_observations",
) -> int:
    if not output.document_ids and not output.link_rows:
        return 0
    with catalogue.remote_transaction():
        inserted_links = _append_missing_link_rows(
            catalogue,
            output.link_rows,
            table_name=links_table,
        )
        inserted_observations = _replace_link_observation_rows(
            catalogue,
            document_ids=output.document_ids,
            rows=output.observation_rows,
            table_name=observations_table,
        )
    return inserted_links + inserted_observations


def _row_batches(
    rows: list[dict[str, object]],
) -> list[list[dict[str, object]]]:
    return [
        rows[index : index + _MERGE_BATCH]
        for index in range(0, len(rows), _MERGE_BATCH)
    ]


def _value_batches(values: list[str], size: int) -> list[list[str]]:
    return [
        values[index : index + size]
        for index in range(0, len(values), size)
    ]


def _arrow_table(
    rows: list[dict[str, object]],
    *,
    map_columns: set[str] = frozenset(),
) -> pa.Table:
    names = rows[0].keys()
    columns: dict[str, pa.Array] = {}
    for name in names:
        values = [row[name] for row in rows]
        if name in map_columns:
            values = [
                list(dict(value or {}).items())
                for value in values
            ]
            columns[name] = pa.array(
                values,
                type=pa.map_(pa.string(), pa.string()),
            )
        else:
            columns[name] = pa.array(values)
    return pa.table(columns)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_string_list(values: set[str]) -> str:
    return ", ".join(_sql_string(value) for value in sorted(values))


def _sql_nullable_string(value: object) -> str:
    return "NULL" if value is None else _sql_string(str(value))


def _sql_nullable_integer(value: object) -> str:
    return "NULL" if value is None else str(int(value))


def _sql_timestamp(value: object) -> str:
    if not isinstance(value, datetime):
        raise TypeError("timestamp SQL values must be datetime instances")
    return f"TIMESTAMPTZ {_sql_string(value.isoformat())}"


def _namespace_name(namespace_uri: str | None) -> str:
    return {
        "http://www.w3.org/1999/xhtml": "HTML",
        "http://www.w3.org/2000/svg": "SVG",
        "http://www.w3.org/1998/Math/MathML": "MathML",
    }.get(namespace_uri, namespace_uri or "HTML")


def _jsonld_type_terms(value: object) -> set[str]:
    terms: set[str] = set()
    if isinstance(value, dict):
        raw_type = value.get("@type")
        if isinstance(raw_type, str):
            terms.add(raw_type)
        elif isinstance(raw_type, list):
            terms.update(item for item in raw_type if isinstance(item, str))
        for child in value.values():
            terms.update(_jsonld_type_terms(child))
    elif isinstance(value, list):
        for child in value:
            terms.update(_jsonld_type_terms(child))
    return terms


def _registrable_domain(hostname: str) -> str | None:
    result = _TLD_EXTRACT(hostname)
    return result.top_domain_under_public_suffix or None


def _relation_scope(source_url: str, target_url: str) -> str:
    source = urlsplit(source_url)
    target = urlsplit(target_url)
    if source_url == target_url:
        return "self"
    source_port = source.port or (80 if source.scheme == "http" else 443)
    target_port = target.port or (80 if target.scheme == "http" else 443)
    if (
        source.scheme,
        source.hostname,
        source_port,
    ) == (
        target.scheme,
        target.hostname,
        target_port,
    ):
        return "same_origin"
    if source.hostname == target.hostname:
        return "same_host"
    source_site = _registrable_domain(source.hostname or "")
    target_site = _registrable_domain(target.hostname or "")
    if source_site is not None and source_site == target_site:
        return "same_site"
    return "external"
