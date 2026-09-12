"""JetStream delivery for complete, visit-scoped materialization."""

from __future__ import annotations

import asyncio
import duckdb
from datetime import UTC, datetime
import json
import logging
from time import monotonic
from typing import Literal
from uuid import UUID

from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.materialization import state
from periplus.retention.identities import write_claims
from periplus.materialization.batch import (
    BatchResult,
    commit_prepared_batch,
    prepare_batch,
)
from periplus.materialization import metrics
from periplus.materialization.validation import validation_statements
from periplus.platform.telemetry import event
from periplus.materialization.contracts import (
    LiveBatchWork,
)
from periplus.materialization.registry import (
    PROJECTIONS,
    REGISTRY_DIGEST,
    RELATIONS,
)
from periplus.materialization.live import run_elected_live_materialization
from periplus.materialization.store import (
    AsyncMaterializationRunStore,
    MaterializationBatch,
    MaterializationRun,
    MaterializationRunStore,
    MaterializationRunStopped,
)
from periplus.materialization.sql import sql_string, sql_string_list
from periplus.platform.catalogue import catalogue_from_env
from periplus.platform.catalogue.public import (
    install_public_catalogue,
    validate_public_catalogue,
)
from periplus.platform.catalogue.operations import (
    is_catalogue_data_corruption,
    is_retryable_catalogue_unavailability,
    is_retryable_catalogue_transaction_conflict,
    run_with_catalogue_retry,
)
from periplus.platform.messaging.catalogue_queue import (
    MATERIALIZATION_ACTIVATE_SUBJECT,
    MATERIALIZATION_BATCH_SUBJECT,
    MATERIALIZATION_PLAN_SUBJECT,
    WORK_STREAM,
    ensure_catalogue_work_stream,
)
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from pydantic import BaseModel, ConfigDict, ValidationError

_PLAN_DURABLE = "periplus-materialization-plan-v1"
_BATCH_DURABLE = "periplus-materialization-batch-v1"
_ACTIVATE_DURABLE = "periplus-materialization-activate-v1"
_BATCH_MAX_ACK_PENDING = 1_024


def generation_table(stage: str, run_id: UUID) -> str:
    return f"_periplus_rebuild_{stage}_{run_id.hex[:16]}"


class PlanWork(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: UUID


class BatchWork(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["rebuild"] = "rebuild"
    batch_id: UUID


class ActivationWork(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: UUID
    completed_batches: int


async def publish_plan(
    jetstream,
    store: AsyncMaterializationRunStore,
    run_id: UUID,
) -> None:
    await jetstream.publish(
        MATERIALIZATION_PLAN_SUBJECT,
        PlanWork(run_id=run_id).model_dump_json().encode(),
        stream=WORK_STREAM,
        headers={"Nats-Msg-Id": f"materialization-plan:{run_id}"},
    )
    await store.mark_plan_published(run_id)


async def publish_batch(
    jetstream,
    store: AsyncMaterializationRunStore,
    batch_id: UUID,
) -> None:
    await jetstream.publish(
        MATERIALIZATION_BATCH_SUBJECT,
        BatchWork(batch_id=batch_id).model_dump_json().encode(),
        stream=WORK_STREAM,
        headers={"Nats-Msg-Id": f"materialization-batch:{batch_id}"},
    )
    await store.mark_batch_published(batch_id)


async def publish_live_batch(jetstream, work: LiveBatchWork) -> None:
    await jetstream.publish(
        MATERIALIZATION_BATCH_SUBJECT,
        work.model_dump_json().encode(),
        stream=WORK_STREAM,
        headers={"Nats-Msg-Id": f"materialization-live:{work.batch_id}"},
    )


async def publish_activation(
    jetstream,
    store: AsyncMaterializationRunStore,
    run_id: UUID,
    *,
    completed_batches: int,
) -> None:
    await jetstream.publish(
        MATERIALIZATION_ACTIVATE_SUBJECT,
        ActivationWork(
            run_id=run_id,
            completed_batches=completed_batches,
        ).model_dump_json().encode(),
        stream=WORK_STREAM,
    )
    await store.mark_activation_published(
        run_id,
        completed_batches=completed_batches,
    )


async def run_materialization(
    jetstream,
    html_repository: RawHtmlRepository,
    *,
    stop: asyncio.Event,
    concurrency: int,
    store: AsyncMaterializationRunStore | None = None,
) -> None:
    if concurrency < 1:
        raise ValueError("materialization concurrency must be positive")
    runs = store or AsyncMaterializationRunStore()
    await ensure_catalogue_work_stream(jetstream)
    plan_subscription = await _subscribe(
        jetstream,
        MATERIALIZATION_PLAN_SUBJECT,
        _PLAN_DURABLE,
        max_ack_pending=1,
    )
    batch_subscription = await _subscribe(
        jetstream,
        MATERIALIZATION_BATCH_SUBJECT,
        _BATCH_DURABLE,
        # This is a shared durable across replicas. Local fetch size bounds
        # each process; a deployment-wide cap must not pin total throughput
        # to one replica's concurrency.
        max_ack_pending=_BATCH_MAX_ACK_PENDING,
    )
    activation_subscription = await _subscribe(
        jetstream,
        MATERIALIZATION_ACTIVATE_SUBJECT,
        _ACTIVATE_DURABLE,
        max_ack_pending=1,
    )
    tasks = [
        asyncio.create_task(
            _plan_loop(plan_subscription, jetstream, runs, stop),
            name="materialization-planner",
        ),
        asyncio.create_task(
            _batch_loop(
                batch_subscription,
                jetstream,
                runs,
                html_repository,
                stop,
                concurrency,
            ),
            name="materialization-batches",
        ),
        asyncio.create_task(
            _activation_loop(
                activation_subscription,
                jetstream,
                runs,
                stop,
            ),
            name="materialization-activation",
        ),
        asyncio.create_task(
            _recover_loop(jetstream, runs, stop),
            name="materialization-recovery-publisher",
        ),
        asyncio.create_task(
            run_elected_live_materialization(
                jetstream,
                stop=stop,
                publish=publish_live_batch,
            ),
            name="materialization-live-cdc",
        ),
    ]
    await asyncio.gather(*tasks)


async def _subscribe(
    jetstream,
    subject: str,
    durable: str,
    *,
    max_ack_pending: int,
):
    return await jetstream.pull_subscribe(
        subject,
        durable=durable,
        stream=WORK_STREAM,
        config=ConsumerConfig(
            durable_name=durable,
            deliver_policy=DeliverPolicy.ALL,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=120,
            max_ack_pending=max_ack_pending,
            filter_subject=subject,
        ),
    )


async def _plan_loop(subscription, jetstream, store, stop) -> None:
    while not stop.is_set():
        message = await _fetch_one(subscription)
        if message is None:
            continue
        try:
            work = PlanWork.model_validate_json(message.data)
            await store.mark_plan_published(work.run_id)
            batches = await _with_heartbeat(
                message,
                asyncio.to_thread(_plan, store._store, work.run_id),
            )
            for batch in batches:
                await publish_batch(jetstream, store, batch.id)
            await message.ack()
            if not batches:
                run = await store.get(work.run_id)
                if run is not None:
                    await publish_activation(
                        jetstream,
                        store,
                        run.id,
                        completed_batches=run.completed_batches,
                    )
        except ValidationError:
            logging.exception("discarding invalid materialization plan work")
            await message.term()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("materialization planning failed; retrying")
            await message.nak(delay=1)


def _plan(store, run_id: UUID) -> tuple[MaterializationBatch, ...]:
    run = store.claim_plan(run_id)
    if run is None or run.status not in {"planning", "running"}:
        return ()
    if run.status == "running":
        return ()
    if run.registry_digest != REGISTRY_DIGEST:
        raise RuntimeError(
            "materialization registry changed after rebuild creation; "
            "start a complete rebuild"
        )
    with catalogue_from_env(threads=1, memory_limit="1GB") as catalogue:
        destinations = {
            spec.name: generation_table(spec.name, run.id)
            for spec in PROJECTIONS
        }
        for spec in PROJECTIONS:
            catalogue.create_materialization_generation(
                spec.relation,
                destinations[spec.name],
            )
        visit_ids = [
            str(row[0])
            for row in catalogue.trusted_remote_rows(
                f"""
                SELECT visit_id::VARCHAR
                FROM ingest.visits AT (VERSION => {run.source_snapshot})
                ORDER BY finished_at NULLS LAST, visit_id
                """
            )
        ]
    batches = [
        (run.source_snapshot, visit_ids[index : index + run.batch_size])
        for index in range(0, len(visit_ids), run.batch_size)
    ]
    _updated, records = store.finish_plan(
        run.id,
        generation_tables=destinations,
        batches=batches,
    )
    return records


async def _batch_loop(
    subscription,
    jetstream,
    store,
    html_repository,
    stop,
    concurrency,
) -> None:
    while not stop.is_set():
        try:
            messages = await subscription.fetch(
                batch=concurrency,
                timeout=1,
            )
        except (NatsTimeoutError, TimeoutError):
            continue
        await asyncio.gather(
            *(
                _handle_batch(
                    message,
                    jetstream,
                    store,
                    html_repository,
                )
                for message in messages
            )
        )


async def _handle_batch(
    message,
    jetstream,
    store: AsyncMaterializationRunStore,
    html_repository: RawHtmlRepository,
) -> None:
    try:
        raw = json.loads(message.data)
        if raw.get("kind") == "live":
            await _handle_live_batch(
                message,
                jetstream,
                store,
                LiveBatchWork.model_validate(raw),
                html_repository,
            )
            return
        work = BatchWork.model_validate(raw)
        await store.mark_batch_published(work.batch_id)
        batch = await store.start_batch(work.batch_id)
        if batch is None:
            await message.term()
            return
        if batch.status != "completed":
            run = await store.get(batch.run_id)
            if run is None or run.status == "failed":
                await message.ack()
                return
            result = await _with_heartbeat(
                message,
                asyncio.to_thread(
                    _execute_batch,
                    html_repository,
                    run,
                    batch,
                ),
            )
            metrics.batch(
                source_items=result.source_items,
                source_bytes=result.source_bytes,
                output_rows=result.output_rows,
                output_bytes=result.output_bytes,
                project_seconds=result.project_seconds,
                parquet_seconds=result.parquet_seconds,
                commit_seconds=result.commit_seconds,
                already_applied=result.already_applied,
                superseded=result.superseded,
            )
            run = await store.complete_batch(
                batch.id,
                source_items=result.source_items,
                source_bytes=result.source_bytes,
                output_rows=result.output_rows,
                output_bytes=result.output_bytes,
            )
            metrics.progress(
                completed=run.completed_batches,
                total=run.total_batches,
            )
        else:
            run = await store.get(batch.run_id)
        if (
            run is not None
            and run.status == "running"
            and run.completed_batches == run.total_batches
        ):
            await publish_activation(
                jetstream,
                store,
                run.id,
                completed_batches=run.completed_batches,
            )
        await _ack_after_durable_outcome(
            message,
            description=f"rebuild batch {batch.id}",
        )
    except MaterializationRunStopped:
        await _ack_after_durable_outcome(message, description="stopped rebuild batch")
    except (json.JSONDecodeError, ValidationError):
        logging.exception("discarding invalid materialization batch work")
        await message.term()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        batch_id = getattr(locals().get("work"), "batch_id", None)
        try:
            if batch_id is None:
                work = BatchWork.model_validate_json(message.data)
                batch_id = work.batch_id
            batch = await store.get_batch(batch_id)
            retryable = _is_retryable_batch_failure(exc)
            exhausted = (
                batch is not None
                and batch.attempts >= 5
                and is_retryable_catalogue_transaction_conflict(exc)
                and not is_retryable_catalogue_unavailability(exc)
            )
            if batch is not None and (not retryable or exhausted):
                logging.exception(
                    "materialization batch failed permanently batch=%s",
                    batch.id,
                )
                failure = RuntimeError(
                    f"batch {batch.id} (ordinal {batch.ordinal}, "
                    f"visits {batch.visit_ids[:3]}) failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                failed = await store.fail(batch.run_id, failure)
                metrics.failure("batch")
                await _try_cleanup_failed_run(store, failed)
                await _ack_after_durable_outcome(
                    message,
                    description=f"failed rebuild batch {batch.id}",
                )
                return
        except Exception:
            logging.exception("failed to record materialization failure")
        logging.exception(
            "materialization batch failed; retrying batch=%s",
            batch_id or "unknown",
        )
        metrics.retry(type(exc).__name__)
        if is_retryable_catalogue_transaction_conflict(exc):
            metrics.conflict()
        await message.nak(delay=1)


async def _handle_live_batch(
    message,
    jetstream,
    store: AsyncMaterializationRunStore,
    work: LiveBatchWork,
    html_repository: RawHtmlRepository,
) -> None:
    try:
        result = await _with_heartbeat(
            message,
            asyncio.to_thread(
                _execute_live_batch,
                html_repository,
                work,
            ),
        )
        metrics.batch(
            source_items=result.source_items,
            source_bytes=result.source_bytes,
            output_rows=result.output_rows,
            output_bytes=result.output_bytes,
            project_seconds=result.project_seconds,
            parquet_seconds=result.parquet_seconds,
            commit_seconds=result.commit_seconds,
            already_applied=result.already_applied,
                superseded=result.superseded,
        )
        await _ack_after_durable_outcome(
            message,
            description=f"live batch {work.batch_id}",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if is_catalogue_data_corruption(exc):
            await _recover_corrupt_generation(
                message,
                jetstream,
                store,
                work,
                exc,
            )
            return
        logging.exception(
            "live materialization batch failed; retrying batch=%s",
            work.batch_id,
        )
        metrics.retry(type(exc).__name__)
        if is_retryable_catalogue_transaction_conflict(exc):
            metrics.conflict()
        await message.nak(delay=1)


async def _recover_corrupt_generation(
    message,
    jetstream,
    store: AsyncMaterializationRunStore,
    work: LiveBatchWork,
    error: BaseException,
) -> None:
    """Fence a corrupt generation after a replacement rebuild is durable."""

    try:
        context = await asyncio.to_thread(
            _generation_recovery_context,
            work.generation_id,
        )
        if context is not None:
            source_snapshot, batch_size = context
            run, created = await store.ensure_rebuild(
                source_snapshot=source_snapshot,
                batch_size=batch_size,
            )
            if created:
                try:
                    await publish_plan(jetstream, store, run.id)
                except Exception:
                    logging.exception(
                        "corruption recovery rebuild %s awaits publication",
                        run.id,
                    )
            invalidated = await asyncio.to_thread(
                _invalidate_generation,
                work.generation_id,
            )
            if invalidated:
                metrics.failure("generation_corrupt")
                logging.error(
                    "material generation %s is corrupt; "
                    "rebuild %s owns recovery: %s",
                    work.generation_id,
                    run.id,
                    error,
                )
        await _ack_after_durable_outcome(
            message,
            description=f"corrupt generation {work.generation_id}",
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logging.exception(
            "failed to coordinate recovery for corrupt material generation %s",
            work.generation_id,
        )
        metrics.retry("generation_recovery")
        await message.nak(delay=2)


def _generation_recovery_context(
    generation_id: UUID,
) -> tuple[int, int] | None:
    current = state.active_generation()
    if current is None or current.id != generation_id:
        return None
    if current.registry_digest != REGISTRY_DIGEST:
        raise RuntimeError("corrupt generation belongs to a different registry")
    with catalogue_from_env(threads=1, memory_limit="512MB") as catalogue:
        return int(catalogue.latest_snapshot() or current.covered_snapshot), current.batch_size


def _invalidate_generation(generation_id: UUID) -> bool:
    with write_claims({"generation": [str(generation_id)]}):
        return state.invalidate_generation(generation_id)


def _execute_batch(html_repository, run, batch):
    with catalogue_from_env(threads=2, memory_limit="2GB") as catalogue:
        return _commit_retained_batch(catalogue, html_repository, run, batch)


def _commit_retained_batch(catalogue, html_repository, run, batch, *, active_generation=False):
    # A retirement can invalidate prepared Parquet. Rebuild that preparation from
    # the remaining identities instead of retrying the same retired rows forever.
    from periplus.retention.identities import EvidenceRetired
    prepared = None
    assert_writable = (None if active_generation else
                       lambda: MaterializationRunStore().assert_writable(run.id))

    def commit():
        nonlocal prepared
        if prepared is None:
            prepared = prepare_batch(catalogue, html_repository, run, batch,
                                     active_generation=active_generation,
                                     assert_writable=assert_writable)
        if isinstance(prepared, BatchResult):
            return prepared
        try:
            return commit_prepared_batch(catalogue, run, batch, prepared,
                                         active_generation=active_generation,
                                         assert_writable=assert_writable)
        except EvidenceRetired as exc:
            prepared = None
            # This is retryable only for a materializer that will reprepare.
            # Ingestion must continue treating retired evidence as terminal.
            raise duckdb.TransactionException("prepared evidence was concurrently retired") from exc

    return run_with_catalogue_retry(commit, description=f"materialization batch {batch.id}",
                                    on_conflict=metrics.conflict)



def _execute_live_batch(
    html_repository: RawHtmlRepository,
    work: LiveBatchWork,
) -> BatchResult:
    tables = {
        spec.name: spec.relation.table
        for spec in PROJECTIONS
    }
    now = datetime.now(UTC)
    run = MaterializationRun(
        id=work.generation_id,
        status="completed",
        source_snapshot=work.snapshot,
        covered_snapshot=work.snapshot,
        activation_snapshot=work.snapshot,
        generation_tables=tables,
        registry_digest=REGISTRY_DIGEST,
        batch_size=max(1, len(work.visit_ids)),
        total_batches=1,
        completed_batches=0,
        source_items=0,
        source_bytes=0,
        output_rows=0,
        output_bytes=0,
        created_at=now,
        started_at=now,
        completed_at=now,
        error=None,
    )
    batch = MaterializationBatch(
        id=work.batch_id,
        run_id=work.generation_id,
        ordinal=work.ordinal,
        snapshot=work.snapshot,
        visit_ids=work.visit_ids,
        status="running",
        attempts=1,
    )
    with catalogue_from_env(threads=2, memory_limit="2GB") as catalogue:
        return _commit_retained_batch(catalogue, html_repository, run, batch, active_generation=True)


async def _activation_loop(subscription, jetstream, store, stop) -> None:
    while not stop.is_set():
        message = await _fetch_one(subscription)
        if message is None:
            continue
        await _handle_activation(message, jetstream, store)


async def _handle_activation(message, jetstream, store) -> None:
    try:
        work = ActivationWork.model_validate_json(message.data)
        await store.mark_activation_published(
            work.run_id,
            completed_batches=work.completed_batches,
        )
        run = await store.claim_activation(work.run_id)
        if run is None:
            completed = await store.get(work.run_id)
            if completed is not None and completed.status == "completed":
                await _with_heartbeat(
                    message,
                    asyncio.to_thread(_finalize_activation, completed),
                )
        else:
            result = await _with_heartbeat(
                message,
                asyncio.to_thread(_activate_or_catch_up, run),
            )
            if isinstance(result, int):
                completed = await store.complete(
                    run.id,
                    activation_snapshot=result,
                )
                await _with_heartbeat(
                    message,
                    asyncio.to_thread(_finalize_activation, completed),
                )
                logging.info(
                    "materialization rebuild activated "
                    "run=%s snapshot=%s rows=%s",
                    completed.id,
                    result,
                    completed.output_rows,
                )
            else:
                through_snapshot, batches = result
                records = await store.add_catchup(
                    run.id,
                    through_snapshot=through_snapshot,
                    visit_id_batches=batches,
                )
                for record in records:
                    await publish_batch(jetstream, store, record.id)
                if not records:
                    refreshed = await store.get(run.id)
                    if refreshed is not None:
                        await publish_activation(
                            jetstream,
                            store,
                            run.id,
                            completed_batches=refreshed.completed_batches,
                        )
        await _ack_after_durable_outcome(
            message,
            description=f"materialization activation {work.run_id}",
        )
    except ValidationError:
        logging.exception("discarding invalid activation work")
        await message.term()
    except asyncio.CancelledError:
        raise
    except Exception:
        logging.exception("materialization activation failed; retrying")
        metrics.retry("activation")
        await message.nak(delay=1)


def _activate_or_catch_up(
    run: MaterializationRun,
) -> int | tuple[int, list[list[str]]]:
    current = state.active_generation()
    generations = [str(run.id)] + ([str(current.id)] if current else [])
    with write_claims({"generation": generations}), catalogue_from_env(threads=1, memory_limit="2GB") as catalogue:
        if run.registry_digest != REGISTRY_DIGEST:
            raise RuntimeError(
                "rebuild registry digest no longer matches the running worker"
            )
        with catalogue.remote_transaction():
            latest = catalogue.latest_snapshot() or run.covered_snapshot
            hidden_generation_exists = not PROJECTIONS or bool(
                catalogue.trusted_remote_rows(
                    "SELECT 1 FROM duckdb_tables() "
                    f"WHERE database_name = {sql_string(catalogue.config.alias)} "
                    "AND schema_name = 'material' AND table_name = "
                    f"{sql_string(
                        run.generation_tables[PROJECTIONS[0].name]
                    )}"
                )
            )
            if hidden_generation_exists:
                changed = (
                    catalogue.trusted_remote_rows(
                        f"""
                        SELECT DISTINCT visit_id::VARCHAR
                        FROM ducklake_table_changes(
                          {sql_string(catalogue.config.alias)},
                          'ingest', 'visits',
                          {run.covered_snapshot + 1}, {latest}
                        )
                        WHERE visit_id IS NOT NULL
                          AND change_type = 'insert'
                        ORDER BY visit_id
                        """
                    )
                    if latest > run.covered_snapshot
                    else []
                )
                if changed:
                    visit_ids = [str(row[0]) for row in changed]
                    return (
                        latest,
                        [
                            visit_ids[index : index + run.batch_size]
                            for index in range(
                                0,
                                len(visit_ids),
                                run.batch_size,
                            )
                        ],
                    )
                _validate_generation(catalogue, run)
            if PROJECTIONS and (hidden_generation_exists or current is None or current.id != run.id):
                catalogue.activate_materialization_generations(
                        {
                            RELATIONS[stage]: table
                            for stage, table in run.generation_tables.items()
                        },
                        activation_id=run.id.hex,
                        transaction=False,
                    )
            install_public_catalogue(catalogue, transaction=False)
        activated = catalogue.last_committed_snapshot() or latest
        state.publish_generation(run, latest)
        return activated


def _finalize_activation(run: MaterializationRun) -> None:
    with catalogue_from_env(threads=1, memory_limit="2GB") as catalogue:
        with catalogue.remote_transaction():
            _verify_active_generation(catalogue, run)
        catalogue.finalize_materialization_activation(
            (spec.relation for spec in PROJECTIONS),
            activation_id=run.id.hex,
        )
        _drop_unregistered_material_relations(catalogue)


async def _recover_loop(jetstream, store, stop) -> None:
    while not stop.is_set():
        try:
            plans, batches, activations, cleanups = await store.recoverable()
            for run_id in plans:
                await publish_plan(jetstream, store, run_id)
            for batch_id in batches:
                await publish_batch(jetstream, store, batch_id)
            for run_id, completed_batches in activations:
                await publish_activation(
                    jetstream,
                    store,
                    run_id,
                    completed_batches=completed_batches,
                )
            for run in cleanups:
                await _try_cleanup_failed_run(store, run)
            info = await jetstream.consumer_info(
                WORK_STREAM,
                _BATCH_DURABLE,
            )
            metrics.queue(
                pending=info.num_pending,
                ack_pending=info.num_ack_pending,
                redelivered=info.num_redelivered,
            )
        except Exception:
            logging.exception("materialization recovery publication failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            pass


def _is_retryable_batch_failure(exc: BaseException) -> bool:
    return (
        is_retryable_catalogue_unavailability(exc)
        or is_retryable_catalogue_transaction_conflict(exc)
    )


async def _try_cleanup_failed_run(
    store: AsyncMaterializationRunStore,
    run: MaterializationRun,
) -> None:
    try:
        await asyncio.to_thread(_cleanup_failed_run, run)
        await store.mark_cleanup_completed(run.id)
    except Exception:
        logging.exception(
            "failed rebuild generation cleanup will retry run=%s",
            run.id,
        )


def _cleanup_failed_run(run: MaterializationRun) -> None:
    if not run.generation_tables:
        return
    # Wait for bounded writers; delayed writers recheck terminal state under
    # this same claim before touching either the dictionary or projection tables.
    with write_claims({"generation": [str(run.id)]}), catalogue_from_env(threads=1, memory_limit="1GB") as catalogue:
        current = MaterializationRunStore().get(run.id)
        if current is None or current.status != "failed":
            return
        catalogue.drop_materialization_generations(
            run.generation_tables.values()
        )


def _validate_generation(
    catalogue,
    run: MaterializationRun,
) -> None:
    if set(run.generation_tables) != set(RELATIONS):
        raise RuntimeError(
            "generation tables do not exactly match the active registry"
        )
    for spec in PROJECTIONS:
        table = run.generation_tables[spec.name]
        rows = catalogue.trusted_remote_rows(
            "SELECT 1 FROM duckdb_tables() "
            f"WHERE database_name = {sql_string(catalogue.config.alias)} "
            "AND schema_name = 'material' "
            f"AND table_name = {sql_string(table)}"
        )
        if not rows:
            raise RuntimeError(f"missing generation relation material.{table}")
        catalogue.trusted_remote_rows(
            f"SELECT * FROM material.{table} LIMIT 0"
        )


def _verify_active_generation(catalogue, run: MaterializationRun) -> None:
    current = state.active_generation()
    if current is None or current.id != run.id or current.registry_digest != REGISTRY_DIGEST:
        raise RuntimeError("active generation state does not match this registry")
    validate_public_catalogue(catalogue)
    for spec in PROJECTIONS:
        identity = ", ".join(spec.identity_columns)
        query = (
            "SELECT count(*) - count(DISTINCT "
            f"({identity})) FROM {spec.relation.qualified}"
        )
        event("materialization_validation_started", operation=spec.name, code="identity")
        duplicates = int(catalogue.trusted_remote_rows(query)[0][0])
        event("materialization_validation_finished", operation=spec.name, code="identity", rows=duplicates)
        if duplicates:
            raise RuntimeError(
                f"{spec.name} contains {duplicates} duplicate identities"
            )
        with validation_statements(catalogue, spec) as statements:
            for query_index, partition, statement in statements:
                started = monotonic()
                fields = dict(operation=spec.name, code=f"validation_{query_index}", attempt=partition)
                event("materialization_validation_started", **fields)
                invalid = int(catalogue.trusted_remote_rows(statement)[0][0])
                event("materialization_validation_finished", **fields,
                      rows=invalid, elapsed_ms=(monotonic() - started) * 1000)
                if invalid:
                    raise RuntimeError(
                        f"{spec.name} validation {query_index} partition {partition} "
                        f"found {invalid} invalid rows"
                    )



def _drop_unregistered_material_relations(catalogue) -> None:
    registered = {spec.relation.table for spec in PROJECTIONS}
    tables = [
        str(row[0])
        for row in catalogue.trusted_remote_rows(
            "SELECT table_name FROM duckdb_tables() "
            f"WHERE database_name = {sql_string(catalogue.config.alias)} "
            "AND schema_name = 'material'"
        )
        if not str(row[0]).startswith("_periplus_")
        and str(row[0]) not in registered
    ]
    if not tables:
        return
    with catalogue.remote_transaction():
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            catalogue.trusted_remote_execute(
                f"DROP TABLE material.{quoted}"
            )


async def _fetch_one(subscription):
    try:
        messages = await subscription.fetch(batch=1, timeout=1)
    except (NatsTimeoutError, TimeoutError):
        return None
    return messages[0] if messages else None


async def _ack_after_durable_outcome(message, *, description: str) -> None:
    """ACK only after commit; let JetStream redeliver when the ACK is lost."""

    try:
        await message.ack()
    except asyncio.CancelledError:
        raise
    except Exception:
        logging.exception(
            "%s is durable but its acknowledgement failed; "
            "redelivery will reconcile from durable state",
            description,
        )


async def _with_heartbeat(message, operation):
    task = asyncio.create_task(operation)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=30)
            if done:
                return task.result()
            await message.in_progress()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
