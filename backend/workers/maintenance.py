"""Atlas off-path catalogue maintenance worker."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import time
from datetime import UTC, datetime

from config import get_bool, get_float, get_int, get_optional, get_str
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.errors import KeyDeletedError, KeyNotFoundError, KeyWrongLastSequenceError
from prometheus_client import start_http_server

from repository.ingestion.health import HealthMonitor, start_health_server
from repository.maintenance import MaintenanceConfig, cleanup_staging, compact
from repository.catalogue.operations import maintenance_lock
from runtime.graph_queue import connect_nats
from runtime.maintenance_queue import (
    MAINTENANCE_CONSUMER,
    MAINTENANCE_LEASE_BUCKET,
    MAINTENANCE_STREAM,
    MaintenanceJob,
    MaintenanceLease,
    MaintenanceOperation,
    MaintenanceWorkerState,
    ensure_maintenance_storage,
    maintenance_operation_id,
)


async def _publish_periodic(jetstream, stop: asyncio.Event, config: MaintenanceConfig) -> None:
    while not stop.is_set():
        epoch_bucket = int(time.time() // config.interval_seconds)
        for kind in ("compact", "cleanup"):
            job = MaintenanceJob(
                operation_id=maintenance_operation_id(kind, epoch_bucket),
                kind=kind,
                requested_at=datetime.now(UTC),
            )
            await jetstream.publish(
                f"atlas.maintenance.{kind}",
                job.model_dump_json().encode(),
                headers={"Nats-Msg-Id": job.operation_id},
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=config.interval_seconds)
        except TimeoutError:
            pass


async def _heartbeat_lease(bucket, revision: int, lease: MaintenanceLease, stop: asyncio.Event) -> None:
    interval = get_float("ATLAS_MAINTENANCE_HEARTBEAT_SECONDS")
    current_revision = revision
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        lease = lease.model_copy(update={"heartbeat_at": datetime.now(UTC)})
        current_revision = await bucket.update(
            "global", lease.model_dump_json().encode(), last=current_revision
        )


async def _process(message, *, worker_id: str, operations, leases, config: MaintenanceConfig) -> None:
    try:
        job = MaintenanceJob.model_validate_json(message.data)
    except Exception:
        await message.term()
        return
    try:
        existing = await operations.get(job.operation_id)
    except (KeyNotFoundError, KeyDeletedError):
        existing = None
    if existing is not None:
        state = MaintenanceOperation.model_validate_json(existing.value)
        if state.status == "completed":
            await message.ack()
            return

    now = datetime.now(UTC)
    lease = MaintenanceLease(
        owner=worker_id,
        operation_id=job.operation_id,
        kind=job.kind,
        acquired_at=now,
        heartbeat_at=now,
    )
    try:
        revision = await leases.create("global", lease.model_dump_json().encode())
    except KeyWrongLastSequenceError:
        await message.nak(delay=5)
        return

    operation = MaintenanceOperation(
        operation_id=job.operation_id,
        kind=job.kind,
        status="running",
        worker_id=worker_id,
        started_at=now,
    )
    if existing is None:
        try:
            await operations.create(job.operation_id, operation.model_dump_json().encode())
        except KeyWrongLastSequenceError:
            pass
    else:
        await operations.put(job.operation_id, operation.model_dump_json().encode())

    heartbeat_stop = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat_lease(leases, revision, lease, heartbeat_stop))
    try:
        if job.kind in {"compact", "flush"}:
            def compact_fenced() -> None:
                with maintenance_lock():
                    compact(config)

            await asyncio.to_thread(compact_fenced)
        elif job.kind == "cleanup":
            await asyncio.to_thread(cleanup_staging, config)
        else:
            raise ValueError(f"maintenance operation {job.kind!r} requires explicit implementation")
    except Exception as exc:
        failed = operation.model_copy(update={
            "status": "failed", "completed_at": datetime.now(UTC), "error": str(exc)
        })
        await operations.put(job.operation_id, failed.model_dump_json().encode())
        logging.exception("maintenance operation %s failed", job.operation_id)
        await message.term()
    else:
        completed = operation.model_copy(update={"status": "completed", "completed_at": datetime.now(UTC)})
        await operations.put(job.operation_id, completed.model_dump_json().encode())
        await message.ack()
    finally:
        heartbeat_stop.set()
        await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            entry = await leases.get("global")
            current = MaintenanceLease.model_validate_json(entry.value)
            if current.owner == worker_id and current.operation_id == job.operation_id:
                await leases.delete("global", last=entry.revision)
        except (KeyNotFoundError, KeyDeletedError, KeyWrongLastSequenceError):
            pass


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for value in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(value, stop.set)
    worker_id = get_optional("ATLAS_MAINTENANCE_WORKER_ID") or f"{os.uname().nodename}:{os.getpid()}"
    config = MaintenanceConfig.from_env()
    client = await connect_nats()
    jetstream = client.jetstream()
    workers, operations, leases = await ensure_maintenance_storage(jetstream)
    subscription = await jetstream.pull_subscribe(
        "atlas.maintenance.*", durable=MAINTENANCE_CONSUMER, stream=MAINTENANCE_STREAM
    )
    started_at = datetime.now(UTC)
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_MAINTENANCE_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    monitor.dependencies_ready()
    health_server, _ = start_health_server(
        address=get_str("ATLAS_MAINTENANCE_WORKER_HEALTH_HOST"),
        port=get_int("ATLAS_MAINTENANCE_WORKER_HEALTH_PORT"),
        monitor=monitor,
    )
    metrics_server = None
    if get_bool("ATLAS_METRICS_ENABLED"):
        metrics_server, _ = start_http_server(
            get_int("ATLAS_MAINTENANCE_WORKER_METRICS_PORT"), addr=get_str("ATLAS_METRICS_HOST")
        )

    async def presence() -> None:
        while not stop.is_set():
            monitor.heartbeat()
            state = MaintenanceWorkerState(
                worker_id=worker_id,
                started_at=started_at,
                last_seen_at=datetime.now(UTC),
            )
            await workers.put(worker_id.replace(":", "-"), state.model_dump_json().encode())
            await asyncio.sleep(5)

    presence_task = asyncio.create_task(presence())
    scheduler = asyncio.create_task(_publish_periodic(jetstream, stop, config))
    try:
        while not stop.is_set():
            try:
                messages = await subscription.fetch(batch=1, timeout=1)
            except (NatsTimeoutError, asyncio.TimeoutError):
                continue
            for message in messages:
                await _process(
                    message, worker_id=worker_id, operations=operations, leases=leases, config=config
                )
    finally:
        stop.set()
        presence_task.cancel()
        scheduler.cancel()
        await asyncio.gather(presence_task, scheduler, return_exceptions=True)
        await asyncio.to_thread(health_server.shutdown)
        health_server.server_close()
        if metrics_server is not None:
            await asyncio.to_thread(metrics_server.shutdown)
            metrics_server.server_close()
        await client.drain()


def main() -> None:
    argparse.ArgumentParser(description="Run the Atlas maintenance worker.").parse_args()
    logging.basicConfig(level=get_str("ATLAS_LOG_LEVEL"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
