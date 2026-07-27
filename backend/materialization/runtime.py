"""Durable execution of bounded materialization maintenance batches."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from uuid import UUID

from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from pydantic import BaseModel, ConfigDict

from materialization.lanes import MaterializationLane, MaterializationLanePool
from materialization.maintenance import (
    BatchResult,
    activate_rebuild,
    materialize_batch,
    materialize_catchup_batch,
    prepare_rebuild,
)
from materialization.store import (
    AsyncMaterializationRunStore,
    MaterializationRun,
)
from repository.objects.html import RawHtmlRepository
from runtime.catalogue_queue import (
    MATERIALIZATION_MAINTENANCE_SUBJECT,
    WORK_STREAM,
    ensure_catalogue_work_stream,
)
from runtime.operation_leases import operation_leases


MAINTENANCE_DURABLE = "atlas-materialization-maintenance-v1"


class MaintenanceWork(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID


async def publish_run(jetstream, run_id: UUID) -> None:
    work = MaintenanceWork(run_id=run_id)
    await jetstream.publish(
        MATERIALIZATION_MAINTENANCE_SUBJECT,
        work.model_dump_json().encode(),
        stream=WORK_STREAM,
        headers={"Nats-Msg-Id": f"materialization:{run_id}"},
    )


async def run_maintenance(
    jetstream,
    leases,
    lane_pool: MaterializationLanePool,
    html_repository: RawHtmlRepository,
    *,
    stop: asyncio.Event,
    store: AsyncMaterializationRunStore | None = None,
) -> None:
    runs = store or AsyncMaterializationRunStore()
    await ensure_catalogue_work_stream(jetstream)
    subscription = await jetstream.pull_subscribe(
        MATERIALIZATION_MAINTENANCE_SUBJECT,
        durable=MAINTENANCE_DURABLE,
        stream=WORK_STREAM,
        config=ConsumerConfig(
            durable_name=MAINTENANCE_DURABLE,
            deliver_policy=DeliverPolicy.ALL,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=30,
            max_ack_pending=1,
            filter_subject=MATERIALIZATION_MAINTENANCE_SUBJECT,
        ),
    )
    repair = asyncio.create_task(
        _publish_queued_runs(jetstream, runs, stop=stop),
        name="materialization-maintenance-publisher",
    )
    try:
        while not stop.is_set():
            try:
                messages = await subscription.fetch(batch=1, timeout=1)
            except (NatsTimeoutError, TimeoutError):
                continue
            if not messages:
                continue
            message = messages[0]
            try:
                work = MaintenanceWork.model_validate_json(message.data)
                done = await _with_ack_heartbeat(
                    message,
                    _process_with_lane(
                        work.run_id,
                        runs,
                        leases,
                        lane_pool,
                        html_repository,
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("materialization maintenance batch failed")
                await message.nak(delay=1)
                continue
            if done:
                await message.ack()
            else:
                await message.nak(delay=0.05)
    finally:
        repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)


async def _process_one_batch(
    run_id: UUID,
    store: AsyncMaterializationRunStore,
    leases,
    lane: MaterializationLane,
    html_repository: RawHtmlRepository,
) -> bool:
    run = await store.start(run_id)
    if run.status == "completed":
        return True
    if run.status == "failed":
        return True
    if run.mode == "rebuild" and not run.destinations:
        destinations = await lane.call(
            prepare_rebuild,
            run.id,
            run.stages,
        )
        await lane.call(_refresh_metadata)
        run = await store.set_destinations(run.id, destinations)
    stage = run.active_stage
    if stage is not None:
        async with _batch_lease(leases, run):
            result = await lane.call(
                _materialize_run_batch,
                html_repository,
                run,
                stage,
            )
        await store.advance(
            run.id,
            stage=stage,
            cursor=result.cursor,
            done=result.done,
            source_items=result.source_items,
            source_bytes=result.source_bytes,
            output_rows=result.output_rows,
        )
        return False
    if run.mode == "rebuild":
        catchup_stage = run.active_catchup_stage
        if catchup_stage is not None:
            result = await lane.call(
                _materialize_catchup_run_batch,
                html_repository,
                run,
                catchup_stage,
            )
            await store.advance_catchup(
                run.id,
                stage=catchup_stage,
                cursor=result.cursor,
                done=result.done,
                source_items=result.source_items,
                source_bytes=result.source_bytes,
                output_rows=result.output_rows,
            )
            return False
        latest_snapshot = await lane.call(_source_highwater, run)
        if latest_snapshot > run.catchup_snapshot:
            await store.begin_catchup(
                run.id,
                target_snapshot=latest_snapshot,
            )
            return False
        async with operation_leases(
            leases,
            tuple(f"material.{stage}" for stage in run.stages),
            phase="materialization",
            acquire_timeout=0,
        ):
            latest_snapshot = await lane.call(_source_highwater, run)
            if latest_snapshot != run.catchup_snapshot:
                await store.begin_catchup(
                    run.id,
                    target_snapshot=latest_snapshot,
                )
                return False
            await lane.call(_activate_run, run)
    await store.complete(run.id)
    logging.info(
        "completed %s materialization run %s stages=%s items=%s rows=%s",
        run.mode,
        run.id,
        ",".join(run.stages),
        run.source_items,
        run.output_rows,
    )
    return True


async def _process_with_lane(
    run_id: UUID,
    store: AsyncMaterializationRunStore,
    leases,
    lane_pool: MaterializationLanePool,
    html_repository: RawHtmlRepository,
) -> bool:
    async with lane_pool.acquire() as lane:
        return await _process_one_batch(
            run_id,
            store,
            leases,
            lane,
            html_repository,
        )


async def _with_ack_heartbeat(message, operation) -> bool:
    task = asyncio.create_task(operation)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=10)
            if done:
                return task.result()
            await message.in_progress()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _materialize_run_batch(
    catalogue,
    html_repository: RawHtmlRepository,
    run: MaterializationRun,
    stage,
):
    return materialize_batch(
        catalogue,
        html_repository,
        stage=stage,
        source_snapshot=run.source_snapshot,
        after_cursor=run.cursors.get(stage),
        item_budget=run.item_budget,
        byte_budget=run.byte_budget,
        destinations=run.destinations,
    )


def _refresh_metadata(catalogue) -> None:
    catalogue.refresh_metadata()


def _materialize_catchup_run_batch(
    catalogue,
    html_repository: RawHtmlRepository,
    run: MaterializationRun,
    stage,
) -> BatchResult:
    if run.catchup_target_snapshot is None:
        raise RuntimeError("catch-up target is not fixed")
    return materialize_catchup_batch(
        catalogue,
        html_repository,
        stage=stage,
        after_snapshot=run.catchup_snapshot,
        through_snapshot=run.catchup_target_snapshot,
        after_cursor=run.catchup_cursors.get(stage),
        item_budget=run.item_budget,
        byte_budget=run.byte_budget,
        destinations=run.destinations,
    )


def _source_highwater(catalogue, run: MaterializationRun) -> int:
    latest = catalogue.latest_snapshot()
    if latest is None or latest <= run.catchup_snapshot:
        return run.catchup_snapshot
    tables: list[str] = []
    stages = set(run.stages)
    if stages & {
        "html_elements",
        "jsonld_values",
        "links",
        "link_observations",
    }:
        tables.append("documents")
    if stages & {"pages", "page_observations"}:
        tables.append("visits")
    maxima = [run.catchup_snapshot]
    alias = "'" + catalogue.config.alias.replace("'", "''") + "'"
    for table in tables:
        rows = catalogue.trusted_remote_rows(
            f"""
            SELECT max(snapshot_id)
            FROM ducklake_table_changes(
              {alias}, 'ingest', '{table}',
              {run.catchup_snapshot + 1}, {latest}
            )
            """
        )
        value = rows[0][0] if rows else None
        if value is not None:
            maxima.append(int(value))
    return max(maxima)


def _activate_run(
    catalogue,
    run: MaterializationRun,
) -> None:
    activate_rebuild(catalogue, run.id, run.destinations)


@asynccontextmanager
async def _batch_lease(leases, run: MaterializationRun):
    if run.mode == "rebuild":
        yield
        return
    stage = run.active_stage
    if stage is None:
        yield
        return
    async with operation_leases(
        leases,
        (f"material.{stage}",),
        phase="materialization",
        acquire_timeout=0,
    ):
        yield


async def _publish_queued_runs(
    jetstream,
    store: AsyncMaterializationRunStore,
    *,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            for run in await store.list(limit=100):
                if run.status == "queued":
                    await publish_run(jetstream, run.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("failed to reconcile queued materialization runs")
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass
