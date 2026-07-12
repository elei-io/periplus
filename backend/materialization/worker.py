from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import logging
import signal

import nats
from nats.errors import TimeoutError as NatsTimeoutError

from config import get_float, get_int, get_str
from materialization.backfill import run_backfill
from materialization.compute import compute_scope
from materialization.fencing import StaleMaterializationJob
from materialization.live import run_crawl_planner, run_live
from materialization.readiness import run_readiness_reconciliation
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
from repository.ingestion.health import HealthMonitor, start_health_server


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for value in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(value, stop.set)

    client = await nats.connect(get_str("NATS_URL"), max_reconnect_attempts=-1)
    jetstream = client.jetstream()
    await ensure_streams(jetstream)
    live_subscription = await jetstream.pull_subscribe(
        SCOPE_LIVE_SUBJECT, durable=SCOPE_LIVE_DURABLE, stream=SCOPE_STREAM
    )
    backfill_subscription = await jetstream.pull_subscribe(
        SCOPE_BACKFILL_SUBJECT,
        durable=SCOPE_BACKFILL_DURABLE,
        stream=SCOPE_STREAM,
    )
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_MATERIALIZATION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    server, _thread = start_health_server(
        address=get_str("ATLAS_MATERIALIZATION_WORKER_HEALTH_HOST"),
        port=get_int("ATLAS_MATERIALIZATION_WORKER_HEALTH_PORT"),
        monitor=monitor,
    )
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(_heartbeat(monitor, stop))
            tasks.create_task(
                _consume_scopes(
                    jetstream, live_subscription, backfill_subscription, stop
                )
            )
            tasks.create_task(run_backfill(jetstream, stop))
            tasks.create_task(run_live(jetstream, stop, monitor))
            tasks.create_task(run_crawl_planner(jetstream, stop))
            tasks.create_task(run_readiness_reconciliation(jetstream, stop))
    finally:
        stop.set()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        await client.close()


async def _consume_scopes(
    jetstream, live_subscription, backfill_subscription, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        messages = await _fetch_prefer_live(live_subscription, backfill_subscription)
        for message in messages:
            started_at = datetime.now(UTC)
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
            except StaleMaterializationJob:
                logging.info("discarding stale materialization scope job")
                await message.ack()
            except Exception as exc:
                logging.exception("materialization scope computation failed")
                await _retry_or_fail(
                    jetstream, message, started_at=started_at, error=exc
                )
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)


async def _fetch_prefer_live(live_subscription, backfill_subscription):
    try:
        return await live_subscription.fetch(batch=1, timeout=0.05)
    except (asyncio.TimeoutError, NatsTimeoutError):
        try:
            return await backfill_subscription.fetch(batch=1, timeout=0.95)
        except (asyncio.TimeoutError, NatsTimeoutError):
            return []


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
    while True:
        await asyncio.sleep(30)
        await message.in_progress()


def main() -> None:
    argparse.ArgumentParser(
        description="Atlas incremental materialization worker"
    ).parse_args()
    logging.basicConfig(
        level=get_str("ATLAS_LOG_LEVEL"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
