"""One-scope-at-a-time live materialization worker."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import logging
import time

import nats
from ducklake_cdc_client import RetryableCDCError
from nats.errors import TimeoutError as NatsTimeoutError
from prometheus_client import start_http_server

from config import get_bool, get_float, get_int, get_str
from materialization.backfill import run_backfill
from materialization.commit import commit_scope, record_scope_failure
from materialization.compute import compute_scope
from materialization.dematerialization import dematerialize_one
from materialization.fencing import StaleMaterializationJob
from materialization.live import run_crawl_planner
from materialization.queue import (
    DEAD_LETTER_SUBJECT,
    SCOPE_BACKFILL_DURABLE,
    SCOPE_BACKFILL_SUBJECT,
    SCOPE_LIVE_DURABLE,
    SCOPE_LIVE_SUBJECT,
    SCOPE_STREAM,
    MaterializationDeadLetter,
    MaterializationFailureJob,
    MaterializationScopeJob,
    ensure_streams,
)
from observability import materialization_metrics
from repository.catalogue import catalogue_from_env
from repository.catalogue.operations import operation_lock, run_with_catalogue_retry
from repository.ingestion.health import HealthMonitor, start_health_server
from runtime.catalogue_lane import run_catalogue_operation
from runtime.operation_leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)
from runtime.resource_governor import (
    DURABLE_RESOURCE_WAIT,
    ResourceCapacityUnavailable,
    ResourcePermitLost,
    catalogue_request,
    ensure_resource_governor_storage,
    object_units,
    resource_permits,
)


async def run(
    initialized: asyncio.Event | None = None, monitor: HealthMonitor | None = None
) -> None:
    stop = asyncio.Event()
    maximum_scope_units = object_units(
        get_int("ATLAS_MATERIALIZATION_MAX_OUTPUT_BYTES")
    )
    catalogue_request(
        "materialization-startup-validation",
        service_class="live",
        object_read_units=maximum_scope_units,
        object_write_units=maximum_scope_units,
    )
    client = await nats.connect(get_str("NATS_URL"), max_reconnect_attempts=-1)
    jetstream = client.jetstream()
    await ensure_streams(jetstream)
    operation_lease_store = await ensure_operation_lease_storage(jetstream)
    resource_grants = await ensure_resource_governor_storage(jetstream)
    live_subscription = await jetstream.pull_subscribe(
        SCOPE_LIVE_SUBJECT, durable=SCOPE_LIVE_DURABLE, stream=SCOPE_STREAM
    )
    backfill_subscription = await jetstream.pull_subscribe(
        SCOPE_BACKFILL_SUBJECT,
        durable=SCOPE_BACKFILL_DURABLE,
        stream=SCOPE_STREAM,
    )
    catalogue = await asyncio.to_thread(catalogue_from_env)
    monitor = monitor or HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_MATERIALIZATION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    monitor.dependencies_ready()
    monitor.subsystem_ready("materialization_scope")
    health_server, _health_thread = start_health_server(
        address=get_str("ATLAS_MATERIALIZATION_WORKER_HEALTH_HOST"),
        port=get_int("ATLAS_MATERIALIZATION_WORKER_HEALTH_PORT"),
        monitor=monitor,
    )
    metrics_server = None
    if get_bool("ATLAS_METRICS_ENABLED"):
        metrics_server, _metrics_thread = start_http_server(
            get_int("ATLAS_MATERIALIZATION_WORKER_METRICS_PORT"),
            addr=get_str("ATLAS_METRICS_HOST"),
        )
    if initialized is not None:
        initialized.set()
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(_heartbeat(monitor, stop))
            tasks.create_task(
                _consume_scopes(
                    jetstream,
                    catalogue,
                    live_subscription,
                    backfill_subscription,
                    stop,
                    operation_lease_store,
                    resource_grants,
                ),
                name="materialization-scopes",
            )
            tasks.create_task(
                _run_leased_subsystem(
                    "backfill",
                    lambda: _supervise_subsystem(
                        "backfill",
                        lambda: run_backfill(jetstream, stop, resource_grants),
                        stop,
                        monitor,
                    ),
                    operation_lease_store,
                    stop,
                    monitor,
                )
            )
            tasks.create_task(
                _run_leased_subsystem(
                    "cdc_crawl_planner",
                    lambda: _supervise_cdc(
                        "cdc_crawl_planner",
                        lambda: run_crawl_planner(
                            jetstream, stop, monitor, resource_grants
                        ),
                        stop,
                        monitor,
                    ),
                    operation_lease_store,
                    stop,
                    monitor,
                )
            )
            tasks.create_task(
                _run_leased_subsystem(
                    "dematerialization",
                    lambda: _run_dematerialization(
                        catalogue, resource_grants, stop
                    ),
                    operation_lease_store,
                    stop,
                    monitor,
                )
            )
            tasks.create_task(_observe_scope_queue(jetstream, stop, monitor))
            tasks.create_task(
                _dependency_probe(client, catalogue, resource_grants, monitor, stop)
            )
    finally:
        stop.set()
        await asyncio.to_thread(catalogue.close)
        await client.close()
        await asyncio.to_thread(health_server.shutdown)
        health_server.server_close()
        if metrics_server is not None:
            await asyncio.to_thread(metrics_server.shutdown)
            metrics_server.server_close()


async def _consume_scopes(
    jetstream,
    catalogue,
    live_subscription,
    backfill_subscription,
    stop: asyncio.Event,
    operation_lease_store,
    resource_grants,
) -> None:
    """Consume one scope at a time; horizontal replicas provide concurrency."""

    while not stop.is_set():
        messages = await _fetch_prefer_live(
            live_subscription, backfill_subscription, batch=1
        )
        for message in messages:
            await _process_scope(
                jetstream,
                catalogue,
                message,
                operation_lease_store,
                resource_grants,
            )


async def _process_scope(
    jetstream,
    catalogue,
    message,
    operation_lease_store,
    resource_grants,
) -> None:
    started_at = datetime.now(UTC)
    operation_started = time.perf_counter()
    outcome = "failed"
    heartbeat = asyncio.create_task(_ack_heartbeat(message))
    try:
        job = MaterializationScopeJob.model_validate_json(message.data)
    except Exception:
        logging.exception("discarding invalid materialization scope job")
        await message.term()
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        return
    service_class = "live" if job.source == "live" else "backfill"
    scope_object_units = object_units(
        get_int("ATLAS_MATERIALIZATION_MAX_OUTPUT_BYTES")
    )
    try:
        async with resource_permits(
            resource_grants,
            catalogue_request(
                job.operation_id,
                service_class=service_class,
                object_read_units=scope_object_units,
                object_write_units=scope_object_units,
            ),
            acquire_timeout=DURABLE_RESOURCE_WAIT,
        ):
            async with operation_leases(
                operation_lease_store,
                (job.operation_id,),
                phase="materialization-scope",
                acquire_timeout=DURABLE_RESOURCE_WAIT,
            ):
                already_committed = await run_catalogue_operation(
                    _scope_succeeded, catalogue, job
                )
                if not already_committed:
                    staged = await run_catalogue_operation(compute_scope, job)
                    await run_catalogue_operation(
                        _commit_scope_fenced, catalogue, staged
                    )
        await message.ack()
        outcome = "succeeded"
    except StaleMaterializationJob:
        logging.info("settling stale materialization scope %s", job.operation_id)
        await message.ack()
        outcome = "stale"
    except (
        OperationLeaseUnavailable,
        OperationLeaseLost,
        ResourceCapacityUnavailable,
        ResourcePermitLost,
    ):
        outcome = "contended"
        await message.nak(delay=1)
    except Exception as exc:
        logging.exception("materialization scope failed")
        await _retry_or_fail(
            jetstream,
            catalogue,
            message,
            job,
            operation_lease_store,
            resource_grants,
            started_at=started_at,
            error=exc,
        )
    finally:
        materialization_metrics.operation(
            phase="scope",
            outcome=outcome,
            duration_seconds=time.perf_counter() - operation_started,
        )
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


def _scope_succeeded(catalogue, job: MaterializationScopeJob) -> bool:
    table = ".".join(
        '"' + part.replace('"', '""') + '"'
        for part in (
            catalogue.config.alias,
            catalogue.config.schema,
            "materialization_scope_results",
        )
    )
    return (
        catalogue.connection.execute(
            f"SELECT 1 FROM {table} WHERE definition_revision_id = ? "
            "AND scope_kind = ? AND scope_id = ? AND operation_id = ? "
            "AND status = 'succeeded' LIMIT 1",
            [
                job.definition_revision_id,
                job.scope_kind,
                job.scope_id,
                job.operation_id,
            ],
        ).fetchone()
        is not None
    )


def _commit_scope_fenced(catalogue, staged):
    def attempt():
        with operation_lock(staged.scope.operation_id):
            return commit_scope(catalogue, staged)

    return run_with_catalogue_retry(
        attempt, description="materialization scope commit"
    )


def _record_scope_failure_fenced(catalogue, failure) -> bool:
    def attempt() -> bool:
        with operation_lock(failure.scope.operation_id):
            return record_scope_failure(catalogue, failure)

    return run_with_catalogue_retry(
        attempt, description="materialization failure coverage commit"
    )


async def _retry_or_fail(
    jetstream,
    catalogue,
    message,
    job: MaterializationScopeJob,
    operation_lease_store,
    resource_grants,
    *,
    started_at: datetime,
    error: Exception,
) -> None:
    deliveries = message.metadata.num_delivered
    maximum = get_int("ATLAS_MATERIALIZATION_MAX_DELIVER")
    if deliveries < maximum:
        await message.nak(delay=min(30, 2 ** max(0, deliveries - 1)))
        return
    failure = MaterializationFailureJob(
        scope=job,
        error=str(error),
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )
    dead_letter = MaterializationDeadLetter(
        job=job,
        error=str(error),
        delivery_count=deliveries,
        failed_at=datetime.now(UTC),
    )
    service_class = "live" if job.source == "live" else "backfill"
    try:
        async with resource_permits(
            resource_grants,
            catalogue_request(
                f"failure:{job.operation_id}",
                service_class=service_class,
                object_write_units=1,
            ),
            acquire_timeout=DURABLE_RESOURCE_WAIT,
        ):
            async with operation_leases(
                operation_lease_store,
                (job.operation_id,),
                phase="materialization-scope",
                acquire_timeout=DURABLE_RESOURCE_WAIT,
            ):
                await run_catalogue_operation(
                    _record_scope_failure_fenced, catalogue, failure
                )
        await jetstream.publish(
            DEAD_LETTER_SUBJECT,
            dead_letter.model_dump_json().encode(),
            headers={"Nats-Msg-Id": f"{job.operation_id}-scope-dead"},
        )
    except Exception:
        logging.exception("failed to persist materialization terminal failure")
        await message.nak(delay=30)
    else:
        await message.term()


async def _run_dematerialization(catalogue, resource_grants, stop: asyncio.Event) -> None:
    while not stop.is_set():
        async with resource_permits(
            resource_grants,
            catalogue_request(
                "materialization-dematerialization",
                service_class="live",
                object_read_units=1,
                object_write_units=1,
            ),
            acquire_timeout=DURABLE_RESOURCE_WAIT,
        ):
            changed = await run_catalogue_operation(dematerialize_one, catalogue)
        await _wait(stop, 0.1 if changed else 5)


async def _fetch_prefer_live(
    live_subscription, backfill_subscription, *, batch: int = 1
):
    try:
        return await live_subscription.fetch(batch=batch, timeout=0.05)
    except (asyncio.TimeoutError, NatsTimeoutError):
        try:
            return await backfill_subscription.fetch(batch=batch, timeout=0.95)
        except (asyncio.TimeoutError, NatsTimeoutError):
            return []


async def _observe_scope_queue(
    jetstream, stop: asyncio.Event, monitor: HealthMonitor
) -> None:
    while not stop.is_set():
        for phase, durable in (
            ("live", SCOPE_LIVE_DURABLE),
            ("backfill", SCOPE_BACKFILL_DURABLE),
        ):
            try:
                info = await jetstream.consumer_info(SCOPE_STREAM, durable)
                pending = int(info.num_pending or 0) + int(info.num_ack_pending or 0)
                queue_age = monitor.queue_observed(
                    f"materialization_{phase}",
                    pending=pending,
                    progress_marker=(
                        getattr(info.delivered, "stream_seq", 0),
                        getattr(info.ack_floor, "stream_seq", 0),
                    ),
                    stalled_after_seconds=get_float("ATLAS_WORKER_QUEUE_STALL_SECONDS"),
                )
                materialization_metrics.queue_state(
                    phase=phase,
                    pending=info.num_pending,
                    ack_pending=info.num_ack_pending,
                    oldest_pending_age_seconds=queue_age,
                )
            except Exception:
                logging.warning(
                    "%s materialization scope queue metrics unavailable",
                    phase,
                    exc_info=True,
                )
        await _wait(stop, 5)


async def _supervise_subsystem(
    name, operation, stop: asyncio.Event, monitor: HealthMonitor
) -> None:
    delay = 1.0
    while not stop.is_set():
        try:
            await operation()
            if not stop.is_set():
                raise RuntimeError(f"{name} exited unexpectedly")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            monitor.subsystem_unavailable(name, str(exc) or type(exc).__name__)
            logging.warning(
                "%s unavailable; retrying in %.1fs", name, delay, exc_info=True
            )
            await _wait(stop, delay)
            delay = min(30.0, delay * 2)
        else:
            return


async def _run_leased_subsystem(
    name,
    operation,
    operation_lease_store,
    stop: asyncio.Event,
    monitor: HealthMonitor,
) -> None:
    while not stop.is_set():
        operation_task = None
        lost_task = None
        stop_task = None
        try:
            async with operation_leases(
                operation_lease_store,
                (name,),
                phase="materialization-scheduler",
                acquire_timeout=0.25,
            ) as guard:
                monitor.subsystem_ready(name)
                operation_task = asyncio.create_task(operation())
                lost_task = asyncio.create_task(guard.wait_lost())
                stop_task = asyncio.create_task(stop.wait())
                done, _pending = await asyncio.wait(
                    (operation_task, lost_task, stop_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if operation_task in done:
                    await operation_task
                    if not stop.is_set():
                        raise RuntimeError(f"{name} exited unexpectedly")
                elif lost_task in done:
                    logging.warning("%s scheduler lease was lost; handing off", name)
                stopping = stop_task in done
                operation_task.cancel()
                await asyncio.gather(operation_task, return_exceptions=True)
                if stopping:
                    return
        except OperationLeaseUnavailable:
            monitor.subsystem_ready(name)
            await _wait(stop, 1)
        except OperationLeaseLost:
            monitor.subsystem_unavailable(name, "scheduler lease was lost")
            await _wait(stop, 1)
        finally:
            for task in (operation_task, lost_task, stop_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (lost_task, stop_task) if task is not None),
                return_exceptions=True,
            )


async def _supervise_cdc(
    name, operation, stop: asyncio.Event, monitor: HealthMonitor
) -> None:
    delay = 1.0
    while not stop.is_set():
        try:
            await operation()
            if not stop.is_set():
                raise RuntimeError(f"{name} exited unexpectedly")
        except asyncio.CancelledError:
            raise
        except RetryableCDCError as exc:
            monitor.subsystem_unavailable(name, str(exc) or type(exc).__name__)
            logging.warning(
                "%s retryable failure; retrying in %.1fs", name, delay, exc_info=True
            )
            await _wait(stop, delay)
            delay = min(30.0, delay * 2)
        except Exception as exc:
            monitor.subsystem_unavailable(name, str(exc) or type(exc).__name__)
            logging.error("%s unexpected failure; retrying in 30s", name, exc_info=True)
            await _wait(stop, 30.0)
        else:
            return


async def _dependency_probe(
    client, catalogue, resource_grants, monitor: HealthMonitor, stop: asyncio.Event
) -> None:
    interval = get_float(
        "ATLAS_MATERIALIZATION_WORKER_HEALTH_PROBE_INTERVAL_SECONDS"
    )
    timeout = get_float(
        "ATLAS_MATERIALIZATION_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS"
    )
    while not stop.is_set():
        try:
            await asyncio.wait_for(client.flush(), timeout=timeout)
            async with asyncio.timeout(timeout):
                async with resource_permits(
                    resource_grants,
                    catalogue_request(
                        "materialization-health-probe", service_class="live"
                    ),
                ):
                    await run_catalogue_operation(
                        catalogue.connection.execute, "SELECT 1"
                    )
        except Exception as exc:
            monitor.dependencies_unavailable(str(exc) or type(exc).__name__)
        else:
            monitor.dependencies_ready()
        await _wait(stop, interval)


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _heartbeat(monitor: HealthMonitor, stop: asyncio.Event) -> None:
    while not stop.is_set():
        monitor.heartbeat()
        await _wait(stop, 1)


async def _ack_heartbeat(message) -> None:
    interval = max(
        1.0, min(30.0, get_float("ATLAS_MATERIALIZATION_ACK_WAIT_SECONDS") / 3)
    )
    while True:
        await asyncio.sleep(interval)
        await message.in_progress()


def main() -> None:
    argparse.ArgumentParser(description="Atlas materialization scope worker").parse_args()
    logging.basicConfig(
        level=get_str("ATLAS_LOG_LEVEL"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
