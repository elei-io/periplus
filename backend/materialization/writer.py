"""DuckLake commit loop owned by the materialization worker process."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from datetime import UTC, datetime

from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.errors import BucketNotFoundError, KeyDeletedError, KeyNotFoundError
from prometheus_client import start_http_server
from sqlalchemy import select

from config import get_bool, get_float, get_int, get_str
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_views.models import CatalogueViewReference
from db.session import session_scope
from materialization.commit import commit_scope_batch, record_scope_failure
from materialization.queue import (
    COMMIT_DURABLE,
    COMMIT_STREAM,
    COMMIT_SUBJECT,
    DEAD_LETTER_SUBJECT,
    SCOPE_LIVE_SUBJECT,
    CrawlMaterializationFanoutPlanJob,
    MaterializationCommitJob,
    MaterializationDeadLetter,
    MaterializationFailureJob,
    ensure_streams,
)
from observability import materialization_metrics
from repository.catalogue.fanout import (
    CrawlMaterializationFanoutStore,
    MissingCrawlMaterializationFanout,
)
from repository.catalogue.materializations import MaterializationStore
from repository.catalogue.operations import (
    operation_lock,
    operation_locks as catalogue_operation_locks,
    run_with_catalogue_retry,
)
from repository.catalogue.views import CatalogueViewStore
from repository.ingestion.health import HealthMonitor, start_health_server
from repository.ingestion.queue import connect_repository_nats
from repository.service import repository_ingestor_from_env
from runtime.maintenance_queue import MAINTENANCE_LEASE_BUCKET
from runtime.catalogue_lane import catalogue_operation_lane
from runtime.operation_leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)


async def run(
    initialized: asyncio.Event | None = None, monitor: HealthMonitor | None = None
) -> None:
    """Consume and commit materialization results with an exclusive connection."""

    stop = asyncio.Event()
    client = await connect_repository_nats()
    jetstream = client.jetstream()
    try:
        maintenance_leases = await jetstream.key_value(MAINTENANCE_LEASE_BUCKET)
    except BucketNotFoundError:
        maintenance_leases = None
    await ensure_streams(jetstream)
    operation_lease_store = await ensure_operation_lease_storage(jetstream)
    subscription = await jetstream.pull_subscribe(
        COMMIT_SUBJECT,
        durable=COMMIT_DURABLE,
        stream=COMMIT_STREAM,
    )
    ingestor = repository_ingestor_from_env()
    await asyncio.to_thread(ingestor.validate)
    health_ingestor = repository_ingestor_from_env()
    await asyncio.to_thread(health_ingestor.validate)
    metrics_server = None
    if get_bool("ATLAS_METRICS_ENABLED"):
        metrics_server, _metrics_thread = start_http_server(
            get_int("ATLAS_MATERIALIZATION_WORKER_METRICS_PORT"),
            addr=get_str("ATLAS_METRICS_HOST"),
        )
    health_monitor = monitor or HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_MATERIALIZATION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    health_monitor.dependencies_ready()
    health_monitor.subsystem_ready("materialization_commit")
    health_server, _health_thread = start_health_server(
        address=get_str("ATLAS_MATERIALIZATION_WORKER_HEALTH_HOST"),
        port=get_int("ATLAS_MATERIALIZATION_WORKER_HEALTH_PORT"),
        monitor=health_monitor,
    )
    if initialized is not None:
        initialized.set()
    heartbeat_task = asyncio.create_task(_health_heartbeat(health_monitor))
    dependency_probe_task = asyncio.create_task(
        _dependency_probe(
            client,
            health_ingestor,
            health_monitor,
            catalogue_operation_lane(),
        )
    )
    catalogue_connection_lock = catalogue_operation_lane()
    commit_task = asyncio.create_task(
        _consume_materialization_commits(
            jetstream,
            subscription,
            ingestor.catalogue,
            catalogue_connection_lock,
            maintenance_leases,
            operation_lease_store,
            stop,
            health_monitor,
        ),
        name="materialization-commits",
    )
    commit_task.add_done_callback(
        lambda task: _report_subsystem_task_exit(
            task, health_monitor, "materialization_commit"
        )
    )
    try:
        while not stop.is_set():
            _raise_if_subsystem_task_exited(commit_task, "materialization commit consumer")
            if not await _maintenance_active(maintenance_leases):
                try:
                    async with operation_leases(
                        operation_lease_store,
                        ("global",),
                        phase="materialization-dematerialize",
                        acquire_timeout=0,
                    ):
                        async with catalogue_connection_lock:
                            await asyncio.to_thread(
                                _dematerialize_requested_materialization_fenced,
                                ingestor.catalogue,
                            )
                except (OperationLeaseUnavailable, OperationLeaseLost):
                    pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.25)
            except TimeoutError:
                pass
    finally:
        stop.set()
        await _cancel_task(commit_task)
        await _cancel_task(heartbeat_task)
        await _cancel_task(dependency_probe_task)
        await asyncio.to_thread(health_server.shutdown)
        health_server.server_close()
        await asyncio.to_thread(health_ingestor.close)
        await asyncio.to_thread(ingestor.close)
        await client.drain()
        if metrics_server is not None:
            await asyncio.to_thread(metrics_server.shutdown)
            metrics_server.server_close()


async def _consume_materialization_commits(
    jetstream,
    subscription,
    catalogue,
    catalogue_connection_lock: asyncio.Lock,
    maintenance_leases,
    operation_lease_store,
    stop: asyncio.Event,
    health_monitor: HealthMonitor,
) -> None:
    """Batch compatible analytical appends with a bounded oldest-item deadline."""

    max_items = get_int("ATLAS_MATERIALIZATION_COMMIT_BATCH_ITEMS")
    max_bytes = get_int("ATLAS_MATERIALIZATION_COMMIT_BATCH_BYTES")
    max_wait = get_float("ATLAS_MATERIALIZATION_COMMIT_BATCH_WAIT_SECONDS")
    if max_items <= 0 or max_bytes <= 0 or max_wait <= 0:
        raise ValueError("materialization commit batch limits must be positive")
    buffers: dict[tuple, list[tuple[object, MaterializationCommitJob, asyncio.Task]]] = {}
    started: dict[tuple, float] = {}
    next_queue_snapshot = 0.0
    try:
        while not stop.is_set():
            if await _maintenance_active(maintenance_leases):
                await asyncio.sleep(0.25)
                continue
            now = time.monotonic()
            due = [
                key
                for key, entries in buffers.items()
                if _materialization_batch_due(
                    entries,
                    started_at=started[key],
                    now=now,
                    max_items=max_items,
                    max_bytes=max_bytes,
                    max_wait=max_wait,
                )
            ]
            for key in due:
                entries = buffers.pop(key)
                started.pop(key, None)
                await _commit_materialization_entries(
                    jetstream,
                                catalogue,
                                entries,
                                catalogue_connection_lock,
                                operation_lease_store,
                )
            if due:
                continue
            if time.monotonic() >= next_queue_snapshot:
                try:
                    info = await jetstream.consumer_info(COMMIT_STREAM, COMMIT_DURABLE)
                    pending = int(info.num_pending or 0) + int(
                        info.num_ack_pending or 0
                    )
                    queue_age = health_monitor.queue_observed(
                        "materialization_commit",
                        pending=pending,
                        progress_marker=(
                            getattr(info.delivered, "stream_seq", 0),
                            getattr(info.ack_floor, "stream_seq", 0),
                        ),
                        stalled_after_seconds=get_float(
                            "ATLAS_WORKER_QUEUE_STALL_SECONDS"
                        ),
                    )
                    materialization_metrics.queue_state(
                        phase="commit",
                        pending=info.num_pending,
                        ack_pending=info.num_ack_pending,
                        oldest_pending_age_seconds=queue_age,
                    )
                except Exception:
                    logging.warning(
                        "materialization commit queue metrics unavailable",
                        exc_info=True,
                    )
                next_queue_snapshot = time.monotonic() + 5
            timeout = 1.0
            if started:
                timeout = max(
                    0.001,
                    min(started_at + max_wait for started_at in started.values())
                    - time.monotonic(),
                )
            try:
                messages = await subscription.fetch(batch=max_items, timeout=timeout)
            except (NatsTimeoutError, asyncio.TimeoutError):
                continue
            for message in messages:
                heartbeat = asyncio.create_task(_heartbeat_messages([message]))
                try:
                    payload = json.loads(message.data)
                    kind = payload.get("kind")
                    if kind == "fanout_plan":
                        plan = CrawlMaterializationFanoutPlanJob.model_validate(payload)
                        try:
                            async with catalogue_connection_lock:
                                async with operation_leases(
                                    operation_lease_store,
                                    (f"crawl-fanout-{plan.crawl_id}",),
                                    phase="materialization-plan",
                                ):
                                    await _commit_fanout_plan(
                                        jetstream, catalogue, plan
                                    )
                        except Exception:
                            logging.exception(
                                "failed to persist crawl materialization fan-out plan"
                            )
                            await message.nak(delay=30)
                        else:
                            await message.ack()
                        await _cancel_task(heartbeat)
                        continue
                    if kind == "failure":
                        failure = MaterializationFailureJob.model_validate(payload)
                        try:
                            async with operation_leases(
                                operation_lease_store,
                                (failure.scope.operation_id,),
                                phase="materialization-commit",
                            ):
                                async with catalogue_connection_lock:
                                    recorded = await asyncio.to_thread(
                                        _record_scope_failure_fenced,
                                        catalogue,
                                        failure,
                                    )
                                if recorded:
                                    await _settle_crawl_fanouts(
                                        catalogue,
                                        failure.scope,
                                        catalogue_connection_lock,
                                        operation_lease_store,
                                    )
                        except (OperationLeaseUnavailable, OperationLeaseLost):
                            await message.nak(delay=1)
                        except Exception:
                            logging.exception(
                                "failed to persist materialization scope failure"
                            )
                            await message.nak(delay=30)
                        else:
                            await message.ack()
                        await _cancel_task(heartbeat)
                        continue
                    job = MaterializationCommitJob.model_validate(payload)
                except Exception:
                    logging.exception("discarding invalid materialization commit job")
                    await message.term()
                    await _cancel_task(heartbeat)
                    continue
                key = (
                    job.scope.materialization_id,
                    job.scope.definition_revision_id,
                    job.scope.target_table,
                    job.scope.scope_kind,
                    job.scope.scope_column,
                    job.scope.source,
                )
                entries = buffers.setdefault(key, [])
                if any(
                    value.scope.scope_id == job.scope.scope_id
                    for _, value, _ in entries
                ):
                    await _commit_materialization_entries(
                        jetstream,
                        catalogue,
                        buffers.pop(key),
                        catalogue_connection_lock,
                        operation_lease_store,
                    )
                    started.pop(key, None)
                    entries = buffers.setdefault(key, [])
                entries.append((message, job, heartbeat))
                started.setdefault(key, time.monotonic())
    finally:
        for entries in buffers.values():
            for _message, _job, heartbeat in entries:
                await _cancel_task(heartbeat)


def _materialization_batch_due(
    entries,
    *,
    started_at: float,
    now: float,
    max_items: int,
    max_bytes: int,
    max_wait: float,
) -> bool:
    return bool(entries) and (
        len(entries) >= max_items
        or sum(job.file_bytes for _message, job, _heartbeat in entries) >= max_bytes
        or now - started_at >= max_wait
    )


async def _commit_materialization_entries(
    jetstream,
    catalogue,
    entries,
    catalogue_connection_lock: asyncio.Lock,
    operation_lease_store,
) -> None:
    """Commit a compatible batch, recursively isolating a bad staged scope."""

    if not entries:
        return
    jobs = [job for _message, job, _heartbeat in entries]
    started_at = time.perf_counter()
    try:

        def commit_fenced():
            def attempt():
                with catalogue_operation_locks(
                    job.scope.operation_id for job in jobs
                ):
                    return commit_scope_batch(catalogue, jobs)

            return run_with_catalogue_retry(
                attempt, description="materialization scope commit"
            )

        async with operation_leases(
            operation_lease_store,
            (job.scope.operation_id for job in jobs),
            phase="materialization-commit",
        ):
            async with catalogue_connection_lock:
                await asyncio.to_thread(commit_fenced)
    except (OperationLeaseUnavailable, OperationLeaseLost):
        materialization_metrics.operation(
            phase="commit",
            outcome="contended",
            duration_seconds=time.perf_counter() - started_at,
        )
        for message, _job, heartbeat in entries:
            await message.nak(delay=1)
            await _cancel_task(heartbeat)
        return
    except Exception as exc:
        if len(entries) > 1:
            midpoint = len(entries) // 2
            await _commit_materialization_entries(
                jetstream,
                catalogue,
                entries[:midpoint],
                catalogue_connection_lock,
                operation_lease_store,
            )
            await _commit_materialization_entries(
                jetstream,
                catalogue,
                entries[midpoint:],
                catalogue_connection_lock,
                operation_lease_store,
            )
            return
        message, job, heartbeat = entries[0]
        materialization_metrics.operation(
            phase="commit",
            outcome="failed",
            duration_seconds=time.perf_counter() - started_at,
        )
        logging.exception(
            "materialization commit failed for operation %s", job.scope.operation_id
        )
        deliveries = message.metadata.num_delivered
        maximum = get_int("ATLAS_MATERIALIZATION_MAX_DELIVER")
        if deliveries < maximum:
            await message.nak(delay=5)
        else:
            failure = MaterializationFailureJob(
                scope=job.scope,
                error=str(exc),
                started_at=job.started_at,
                completed_at=datetime.now(UTC),
            )
            dead_letter = MaterializationDeadLetter(
                job=job.scope,
                stage="commit",
                error=str(exc),
                delivery_count=deliveries,
                failed_at=datetime.now(UTC),
                staging_key=job.staging_key,
            )
            try:
                async with catalogue_connection_lock:
                    await asyncio.to_thread(
                        _record_scope_failure_fenced, catalogue, failure
                    )
                await jetstream.publish(
                    DEAD_LETTER_SUBJECT,
                    dead_letter.model_dump_json().encode(),
                    headers={
                        "Nats-Msg-Id": f"{job.scope.operation_id}-commit-dead"
                    },
                )
            except Exception:
                logging.exception("failed to persist materialization dead letter")
                await message.nak(delay=30)
            else:
                await message.term()
        await _cancel_task(heartbeat)
        return
    materialization_metrics.operation(
        phase="commit",
        outcome="succeeded",
        duration_seconds=time.perf_counter() - started_at,
    )
    for message, job, heartbeat in entries:
        try:
            await _settle_crawl_fanouts(
                catalogue,
                job.scope,
                catalogue_connection_lock,
                operation_lease_store,
            )
            await message.ack()
        except Exception as exc:
            logging.exception(
                "materialization committed but crawl fan-out settlement failed for "
                "operation %s",
                job.scope.operation_id,
            )
            await _retry_or_dead_letter_settlement(
                jetstream, message, job, error=exc
            )
        finally:
            await _cancel_task(heartbeat)


async def _retry_or_dead_letter_settlement(
    jetstream, message, job: MaterializationCommitJob, *, error: Exception
) -> None:
    deliveries = message.metadata.num_delivered
    maximum = get_int("ATLAS_MATERIALIZATION_MAX_DELIVER")
    if deliveries < maximum:
        try:
            await message.nak(delay=5)
        except Exception:
            logging.exception(
                "failed to request materialization settlement redelivery for "
                "operation %s",
                job.scope.operation_id,
            )
        return
    dead_letter = MaterializationDeadLetter(
        job=job.scope,
        stage="settlement",
        error=str(error),
        delivery_count=deliveries,
        failed_at=datetime.now(UTC),
        staging_key=job.staging_key,
    )
    try:
        await jetstream.publish(
            DEAD_LETTER_SUBJECT,
            dead_letter.model_dump_json().encode(),
            headers={
                "Nats-Msg-Id": f"{job.scope.operation_id}-settlement-dead"
            },
        )
    except Exception:
        logging.exception(
            "failed to persist materialization settlement dead letter for operation %s",
            job.scope.operation_id,
        )
        await message.nak(delay=30)
    else:
        await message.term()


async def _settle_crawl_fanouts(
    catalogue,
    scope,
    catalogue_connection_lock: asyncio.Lock,
    operation_lease_store,
) -> None:
    store = CrawlMaterializationFanoutStore(catalogue)
    async with catalogue_connection_lock:
        crawl_ids = await asyncio.to_thread(
            store.crawls_for_scope,
            materialization_id=scope.materialization_id,
            definition_revision_id=scope.definition_revision_id,
            scope_kind=scope.scope_kind,
            scope_id=scope.scope_id,
        )
    for crawl_id in crawl_ids:
        async with operation_leases(
            operation_lease_store,
            (str(crawl_id),),
            phase="materialization-fanout-settlement",
        ):
            async with catalogue_connection_lock:
                try:
                    await asyncio.to_thread(
                        _refresh_crawl_fanout_fenced,
                        catalogue,
                        crawl_id,
                    )
                except MissingCrawlMaterializationFanout:
                    logging.info(
                        "materialization scope settled before crawl %s fan-out plan; "
                        "planner reconciliation will settle it",
                        crawl_id,
                    )


def _refresh_crawl_fanout_fenced(catalogue, crawl_id):
    def attempt():
        with operation_lock(f"crawl-fanout-settle:{crawl_id}"):
            return CrawlMaterializationFanoutStore(catalogue).refresh(crawl_id)

    return run_with_catalogue_retry(
        attempt, description="crawl materialization fan-out settlement"
    )


async def _commit_fanout_plan(jetstream, catalogue, plan) -> None:
    from repository.catalogue.records import CrawlMaterializationFanoutMember

    members = [
        CrawlMaterializationFanoutMember(
            crawl_id=plan.crawl_id,
            materialization_id=scope.materialization_id,
            definition_revision_id=scope.definition_revision_id,
            scope_kind=scope.scope_kind,
            scope_id=scope.scope_id,
        )
        for scope in plan.scopes
    ]

    def plan_fenced():
        def attempt():
            with operation_lock(f"crawl-fanout-{plan.crawl_id}"):
                store = CrawlMaterializationFanoutStore(catalogue)
                store.plan(
                    plan.crawl_id,
                    members=members,
                )
                return store.refresh(plan.crawl_id)

        return run_with_catalogue_retry(
            attempt, description="crawl materialization fan-out plan"
        )

    await asyncio.to_thread(plan_fenced)
    for scope in plan.scopes:
        await jetstream.publish(
            SCOPE_LIVE_SUBJECT,
            scope.model_dump_json().encode(),
            headers={"Nats-Msg-Id": scope.operation_id},
        )


def _dematerialize_requested_materialization(catalogue) -> None:
    with session_scope() as session:
        model = session.scalar(
            select(CatalogueMaterialization)
            .where(
                CatalogueMaterialization.archived_at.is_(None),
                CatalogueMaterialization.dematerialization_requested_at.is_not(None),
            )
            .order_by(CatalogueMaterialization.dematerialization_requested_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if model is None:
            return
        reference = session.get(CatalogueViewReference, model.view_reference_id)
        if reference is None:
            raise RuntimeError(
                "Materialized view reference is missing during dematerialization"
            )
        view_store = CatalogueViewStore(catalogue)
        current = view_store.get(reference.ducklake_view_uuid)
        if current is None:
            raise RuntimeError("Materialized view is missing during dematerialization")
        restored = view_store.replace(
            current_uuid=current.view_uuid, sql=model.source_sql
        )
        reference.ducklake_view_uuid = restored.view_uuid
        present = any(
            table.table_name == model.name
            for table in catalogue.lake.table.list(
                schema_name="_atlas_materializations"
            )
        )
        if present:
            MaterializationStore(catalogue).drop_managed(
                name=model.name,
                expected_uuid=model.ducklake_table_uuid,
                materialization_id=model.id,
            )
        model.archived_at = datetime.now(UTC)
        session.flush()


def _dematerialize_requested_materialization_fenced(catalogue) -> None:
    def attempt() -> None:
        with operation_lock("materialization-dematerialization"):
            _dematerialize_requested_materialization(catalogue)

    run_with_catalogue_retry(
        attempt, description="materialization dematerialization commit"
    )


def _record_scope_failure_fenced(catalogue, failure) -> bool:
    def attempt() -> bool:
        with operation_lock(failure.scope.operation_id):
            return record_scope_failure(catalogue, failure)

    return run_with_catalogue_retry(
        attempt, description="materialization failure coverage commit"
    )


async def _heartbeat_messages(messages) -> None:
    interval = max(
        1.0,
        min(30.0, get_float("ATLAS_MATERIALIZATION_ACK_WAIT_SECONDS") / 3),
    )
    while True:
        await asyncio.sleep(interval)
        await asyncio.gather(
            *(message.in_progress() for message in messages),
            return_exceptions=True,
        )


async def _health_heartbeat(monitor: HealthMonitor) -> None:
    while True:
        monitor.heartbeat()
        await asyncio.sleep(1)


async def _maintenance_active(bucket) -> bool:
    if bucket is None:
        return False
    try:
        await bucket.get("global")
    except (KeyNotFoundError, KeyDeletedError):
        return False
    except Exception:
        logging.warning(
            "maintenance lease state unavailable; pausing materialization writes",
            exc_info=True,
        )
        return True
    return True


async def _dependency_probe(
    client,
    ingestor,
    monitor: HealthMonitor,
    catalogue_connection_lock: asyncio.Lock,
) -> None:
    interval = get_float(
        "ATLAS_MATERIALIZATION_WORKER_HEALTH_PROBE_INTERVAL_SECONDS"
    )
    timeout = get_float(
        "ATLAS_MATERIALIZATION_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS"
    )
    if interval <= 0 or timeout <= 0:
        monitor.dependencies_unavailable(
            "materialization health probe intervals must be greater than zero"
        )
        return
    while True:
        try:
            await asyncio.wait_for(client.flush(), timeout=timeout)
            async with asyncio.timeout(timeout):
                async with catalogue_connection_lock:
                    await asyncio.to_thread(ingestor.validate)
        except Exception as exc:
            monitor.dependencies_unavailable(str(exc) or type(exc).__name__)
        else:
            monitor.dependencies_ready()
        await asyncio.sleep(interval)


async def _cancel_task(task) -> None:
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _report_subsystem_task_exit(
    task: asyncio.Task, monitor: HealthMonitor, subsystem: str
) -> None:
    if task.cancelled():
        return
    error = task.exception()
    detail = (
        str(error) or type(error).__name__
        if error is not None
        else "subsystem exited unexpectedly"
    )
    monitor.subsystem_unavailable(subsystem, detail)


def _raise_if_subsystem_task_exited(task: asyncio.Task, description: str) -> None:
    if not task.done():
        return
    if task.cancelled():
        raise RuntimeError(f"{description} was cancelled unexpectedly")
    error = task.exception()
    if error is None:
        raise RuntimeError(f"{description} exited unexpectedly")
    raise RuntimeError(f"{description} failed") from error


def main() -> None:
    argparse.ArgumentParser(
        description="Run the Atlas materialization commit loop."
    ).parse_args()
    logging.basicConfig(
        level=get_str("ATLAS_LOG_LEVEL"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
