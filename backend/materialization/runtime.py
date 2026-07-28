"""Durable execution of bounded materialization maintenance batches."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from pydantic import BaseModel, ConfigDict, ValidationError
from repository.objects.html import RawHtmlRepository
from runtime.catalogue_queue import (
    MATERIALIZATION_MAINTENANCE_SUBJECT,
    WORK_STREAM,
    ensure_catalogue_work_stream,
)
from runtime.operation_leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    operation_leases,
)

from materialization.contracts import (
    DOCUMENT_PROJECTIONS,
    workload_projections,
)
from materialization.lanes import MaterializationLanePool
from materialization.maintenance import (
    BatchResult,
    activate_rebuild,
    finalize_rebuild,
    materialize_document_batch,
    materialize_visit_batch,
    prepare_rebuild,
)
from materialization.store import (
    AsyncMaterializationRunStore,
    MaterializationRun,
)

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
            except ValidationError:
                logging.exception(
                    "discarding invalid materialization maintenance work"
                )
                await message.term()
                continue
            try:
                done = await _with_ack_heartbeat(
                    message,
                    _process_one_batch(
                        work.run_id,
                        runs,
                        leases,
                        lane_pool,
                        html_repository,
                    ),
                )
            except asyncio.CancelledError:
                raise
            except (OperationLeaseLost, OperationLeaseUnavailable):
                logging.info(
                    "materialization maintenance lease is unavailable; "
                    "retrying",
                    exc_info=True,
                )
                await message.nak(delay=1)
                continue
            except Exception as exc:
                logging.exception(
                    "materialization maintenance run failed permanently"
                )
                try:
                    run = await runs.fail(work.run_id, exc)
                    if run.status == "completed":
                        await message.nak(delay=1)
                        continue
                    if run.mode == "rebuild" and run.destinations:
                        await lane_pool.call(
                            _discard_rebuild,
                            run.destinations,
                        )
                except Exception:
                    logging.exception(
                        "failed to record or clean up failed "
                        "materialization run"
                    )
                    await message.nak(delay=1)
                    continue
                await message.ack()
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
    lane_pool: MaterializationLanePool,
    html_repository: RawHtmlRepository,
) -> bool:
    try:
        run = await store.start(run_id)
    except KeyError:
        logging.warning(
            "discarding materialization maintenance work for unknown run %s",
            run_id,
        )
        return True
    if run.status == "completed":
        if run.mode == "rebuild":
            await lane_pool.call(_finalize_run, run)
        return True
    if run.status == "failed":
        if run.mode == "rebuild" and run.destinations:
            await lane_pool.call(
                _discard_rebuild,
                run.destinations,
            )
        return True
    if run.mode == "rebuild" and not run.destinations:
        destinations = await lane_pool.call(
            prepare_rebuild,
            run.id,
            run.stages,
        )
        await lane_pool.call_all(_refresh_metadata)
        run = await store.set_destinations(run.id, destinations)
    stage = run.active_stage
    if stage is not None:
        stages = workload_projections(run.stages, stage)
        result = await _materialize_run_batch(
            leases,
            lane_pool,
            html_repository,
            run,
            stages,
        )
        await store.advance(
            run.id,
            stages=stages,
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
            stages = workload_projections(run.stages, catchup_stage)
            result = await _materialize_catchup_run_batch(
                leases,
                lane_pool,
                html_repository,
                run,
                stages,
            )
            await store.advance_catchup(
                run.id,
                stages=stages,
                cursor=result.cursor,
                done=result.done,
                source_items=result.source_items,
                source_bytes=result.source_bytes,
                output_rows=result.output_rows,
            )
            return False
        latest_snapshot = await lane_pool.call(_source_highwater, run)
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
            latest_snapshot = await lane_pool.call(_source_highwater, run)
            if latest_snapshot != run.catchup_snapshot:
                await store.begin_catchup(
                    run.id,
                    target_snapshot=latest_snapshot,
                )
                return False
            await lane_pool.call(_activate_run, run)
    run = await store.complete(run.id)
    if run.mode == "rebuild":
        await lane_pool.call(_finalize_run, run)
    logging.info(
        "completed %s materialization run %s stages=%s items=%s rows=%s",
        run.mode,
        run.id,
        ",".join(run.stages),
        run.source_items,
        run.output_rows,
    )
    return True


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


async def _materialize_run_batch(
    leases,
    lane_pool: MaterializationLanePool,
    html_repository: RawHtmlRepository,
    run: MaterializationRun,
    stages,
) -> BatchResult:
    cursor = run.cursors.get(stages[0])
    if stages[0] in DOCUMENT_PROJECTIONS:
        return await materialize_document_batch(
            leases,
            lane_pool,
            html_repository,
            stages=stages,
            source_snapshot=run.source_snapshot,
            after_cursor=cursor,
            item_budget=run.item_budget,
            byte_budget=run.byte_budget,
            destinations=run.destinations,
        )
    async with operation_leases(
        leases,
        tuple(f"material.{stage}" for stage in stages),
        phase="materialization",
        acquire_timeout=0,
    ):
        return await lane_pool.call(
            _materialize_visit_on_lane,
            {
                "stages": stages,
                "source_snapshot": run.source_snapshot,
                "after_cursor": cursor,
                "item_budget": run.item_budget,
                "destinations": run.destinations,
            },
        )


def _refresh_metadata(catalogue) -> None:
    catalogue.refresh_metadata()


async def _materialize_catchup_run_batch(
    leases,
    lane_pool: MaterializationLanePool,
    html_repository: RawHtmlRepository,
    run: MaterializationRun,
    stages,
) -> BatchResult:
    if run.catchup_target_snapshot is None:
        raise RuntimeError("catch-up target is not fixed")
    cursor = run.catchup_cursors.get(stages[0])
    if stages[0] in DOCUMENT_PROJECTIONS:
        return await materialize_document_batch(
            leases,
            lane_pool,
            html_repository,
            stages=stages,
            source_snapshot=run.source_snapshot,
            after_snapshot=run.catchup_snapshot,
            through_snapshot=run.catchup_target_snapshot,
            after_cursor=cursor,
            item_budget=run.item_budget,
            byte_budget=run.byte_budget,
            destinations=run.destinations,
        )
    async with operation_leases(
        leases,
        tuple(f"material.{stage}" for stage in stages),
        phase="materialization",
        acquire_timeout=0,
    ):
        return await lane_pool.call(
            _materialize_visit_on_lane,
            {
                "stages": stages,
                "source_snapshot": run.source_snapshot,
                "after_snapshot": run.catchup_snapshot,
                "through_snapshot": run.catchup_target_snapshot,
                "after_cursor": cursor,
                "item_budget": run.item_budget,
                "destinations": run.destinations,
            },
        )


def _materialize_visit_on_lane(catalogue, scope: dict) -> BatchResult:
    return materialize_visit_batch(catalogue, **scope)


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


def _finalize_run(
    catalogue,
    run: MaterializationRun,
) -> None:
    finalize_rebuild(catalogue, run.id, run.destinations)


def _discard_rebuild(catalogue, destinations: dict[str, str]) -> None:
    catalogue.drop_materialization_generations(destinations.values())


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
