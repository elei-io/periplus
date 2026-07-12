from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import logging
import time

import nats
from ducklake_cdc_client import RetryableCDCError
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.errors import BucketNotFoundError, KeyDeletedError, KeyNotFoundError

from config import get_float, get_int, get_str
from observability import materialization_metrics
from materialization.backfill import run_backfill
from materialization.compute import compute_scope
from materialization.fencing import StaleMaterializationJob
from materialization.live import run_crawl_planner, run_live
from materialization.queue import (
    COMMIT_SUBJECT,
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
from repository.ingestion.health import HealthMonitor
from runtime.maintenance_queue import MAINTENANCE_LEASE_BUCKET


async def run(
    initialized: asyncio.Event | None = None, monitor: HealthMonitor | None = None
) -> None:
    if initialized is not None:
        await initialized.wait()
    stop = asyncio.Event()
    client = await nats.connect(get_str("NATS_URL"), max_reconnect_attempts=-1)
    jetstream = client.jetstream()
    try:
        maintenance_leases = await jetstream.key_value(MAINTENANCE_LEASE_BUCKET)
    except BucketNotFoundError:
        maintenance_leases = None
    await ensure_streams(jetstream)
    live_subscription = await jetstream.pull_subscribe(
        SCOPE_LIVE_SUBJECT, durable=SCOPE_LIVE_DURABLE, stream=SCOPE_STREAM
    )
    backfill_subscription = await jetstream.pull_subscribe(
        SCOPE_BACKFILL_SUBJECT,
        durable=SCOPE_BACKFILL_DURABLE,
        stream=SCOPE_STREAM,
    )
    monitor = monitor or HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_CATALOG_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    monitor.subsystem_ready("materialization_compute")
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(_heartbeat(monitor, stop))
            tasks.create_task(
                _consume_scopes(
                    jetstream,
                    live_subscription,
                    backfill_subscription,
                    stop,
                    maintenance_leases,
                    concurrency=max(1, get_int("ATLAS_CATALOG_WORKER_CONCURRENCY")),
                ),
                name="materialization-compute",
            )
            tasks.create_task(run_backfill(jetstream, stop))
            tasks.create_task(
                _supervise_cdc(
                    "cdc_live", lambda: run_live(jetstream, stop, monitor), stop, monitor
                )
            )
            tasks.create_task(
                _supervise_cdc(
                    "cdc_crawl_planner",
                    lambda: run_crawl_planner(jetstream, stop, monitor),
                    stop,
                    monitor,
                )
            )
            tasks.create_task(_observe_scope_queue(jetstream, stop))
    finally:
        stop.set()
        await client.close()


async def _consume_scopes(
    jetstream,
    live_subscription,
    backfill_subscription,
    stop: asyncio.Event,
    maintenance_leases,
    *,
    concurrency: int,
) -> None:
    active: set[asyncio.Task] = set()
    try:
        while not stop.is_set():
            active = {task for task in active if not task.done()}
            available = concurrency - len(active)
            if available <= 0:
                await asyncio.sleep(0.01)
                continue
            if await _maintenance_active(maintenance_leases):
                await asyncio.sleep(0.25)
                continue
            messages = await _fetch_prefer_live(
                live_subscription, backfill_subscription, batch=available
            )
            for message in messages:
                active.add(asyncio.create_task(_compute_message(jetstream, message)))
    finally:
        await asyncio.gather(*active, return_exceptions=True)


async def _compute_message(jetstream, message) -> None:
    started_at = datetime.now(UTC)
    compute_started = time.perf_counter()
    outcome = "failed"
    heartbeat = asyncio.create_task(_ack_heartbeat(message))
    try:
        job = MaterializationScopeJob.model_validate_json(message.data)
        commit = await asyncio.to_thread(compute_scope, job)
        await jetstream.publish(
            COMMIT_SUBJECT,
            commit.model_dump_json().encode(),
            headers={"Nats-Msg-Id": job.operation_id},
        )
        await message.ack()
        outcome = "succeeded"
    except StaleMaterializationJob as exc:
        logging.info("settling stale materialization scope job")
        failure = MaterializationFailureJob(
            scope=job,
            reason="stale",
            error=str(exc),
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
        await jetstream.publish(
            COMMIT_SUBJECT,
            failure.model_dump_json().encode(),
            headers={"Nats-Msg-Id": f"{job.operation_id}-stale-failure"},
        )
        await message.ack()
        outcome = "stale"
    except Exception as exc:
        outcome = "failed"
        logging.exception("materialization scope computation failed")
        await _retry_or_fail(jetstream, message, started_at=started_at, error=exc)
    finally:
        materialization_metrics.operation(
            phase="compute",
            outcome=outcome,
            duration_seconds=time.perf_counter() - compute_started,
        )
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


async def _fetch_prefer_live(live_subscription, backfill_subscription, *, batch: int = 1):
    try:
        return await live_subscription.fetch(batch=batch, timeout=0.05)
    except (asyncio.TimeoutError, NatsTimeoutError):
        try:
            return await backfill_subscription.fetch(batch=batch, timeout=0.95)
        except (asyncio.TimeoutError, NatsTimeoutError):
            return []


async def _observe_scope_queue(jetstream, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            info = await jetstream.consumer_info(SCOPE_STREAM, SCOPE_LIVE_DURABLE)
            materialization_metrics.queue_state(
                phase="compute",
                pending=info.num_pending,
                ack_pending=info.num_ack_pending,
            )
        except Exception:
            logging.warning("materialization scope queue metrics unavailable", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass


async def _supervise_subsystem(
    name, operation, stop: asyncio.Event, monitor: HealthMonitor
) -> None:
    """Keep one background subsystem failure local to that subsystem."""

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
            logging.warning("%s unavailable; retrying in %.1fs", name, delay, exc_info=True)
            await _wait(stop, delay)
            delay = min(30.0, delay * 2)
        else:
            return


async def _supervise_cdc(
    name, operation, stop: asyncio.Event, monitor: HealthMonitor
) -> None:
    """Retry typed CDC lifecycle failures without stopping sibling workloads."""

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
            # CDC remains an isolated subsystem even for a new upstream failure class.
            monitor.subsystem_unavailable(name, str(exc) or type(exc).__name__)
            logging.error(
                "%s unexpected failure; retrying in 30s", name, exc_info=True
            )
            await _wait(stop, 30.0)
        else:
            return


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _maintenance_active(bucket) -> bool:
    if bucket is None:
        return False
    try:
        await bucket.get("global")
    except (KeyNotFoundError, KeyDeletedError):
        return False
    return True


async def _retry_or_fail(
    jetstream, message, *, started_at: datetime, error: Exception
) -> None:
    deliveries = message.metadata.num_delivered
    maximum = get_int("ATLAS_MATERIALIZATION_MAX_DELIVER")
    if deliveries < maximum:
        await message.nak(delay=5)
        return
    try:
        job = MaterializationScopeJob.model_validate_json(message.data)
    except Exception:
        await message.term()
        return
    failure = MaterializationFailureJob(
        scope=job,
        error=str(error),
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )
    dead_letter = MaterializationDeadLetter(
        job=job,
        stage="compute",
        error=str(error),
        delivery_count=deliveries,
        failed_at=datetime.now(UTC),
    )
    await jetstream.publish(
        COMMIT_SUBJECT,
        failure.model_dump_json().encode(),
        headers={"Nats-Msg-Id": f"{job.operation_id}-failure"},
    )
    await jetstream.publish(
        DEAD_LETTER_SUBJECT,
        dead_letter.model_dump_json().encode(),
        headers={"Nats-Msg-Id": f"{job.operation_id}-compute-dead"},
    )
    await message.term()


async def _heartbeat(monitor: HealthMonitor, stop: asyncio.Event) -> None:
    while not stop.is_set():
        monitor.heartbeat()
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass


async def _ack_heartbeat(message) -> None:
    interval = max(
        1.0, min(30.0, get_float("ATLAS_MATERIALIZATION_ACK_WAIT_SECONDS") / 3)
    )
    while True:
        await asyncio.sleep(interval)
        await message.in_progress()


def main() -> None:
    argparse.ArgumentParser(
        description="Atlas catalog materialization executor"
    ).parse_args()
    logging.basicConfig(
        level=get_str("ATLAS_LOG_LEVEL"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
