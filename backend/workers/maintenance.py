"""Atlas off-path catalogue maintenance worker."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from datetime import UTC, datetime
from uuid import uuid4

from config import get_bool, get_float, get_int, get_optional, get_str
from nats.js.errors import KeyDeletedError, KeyNotFoundError, KeyWrongLastSequenceError
from prometheus_client import start_http_server

from repository.catalogue.operations import maintenance_lock
from repository.ingestion.health import HealthMonitor, start_health_server
from repository.maintenance import MaintenanceConfig, cleanup_staging, compact
from runtime.graph_queue import connect_nats
from runtime.maintenance_queue import (
    MaintenanceKind,
    MaintenanceLease,
    ensure_maintenance_storage,
)


async def _heartbeat_lease(
    bucket, revision: int, lease: MaintenanceLease, stop: asyncio.Event
) -> None:
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


async def _run_operation(
    *, kind: MaintenanceKind, worker_id: str, leases, config: MaintenanceConfig
) -> None:
    now = datetime.now(UTC)
    lease = MaintenanceLease(
        owner=worker_id,
        operation_id=f"{kind}-{uuid4().hex}",
        kind=kind,
        acquired_at=now,
        heartbeat_at=now,
    )
    try:
        revision = await leases.create("global", lease.model_dump_json().encode())
    except KeyWrongLastSequenceError:
        return

    heartbeat_stop = asyncio.Event()
    heartbeat = asyncio.create_task(
        _heartbeat_lease(leases, revision, lease, heartbeat_stop)
    )
    try:
        if kind == "compact":
            def compact_fenced() -> None:
                with maintenance_lock():
                    compact(config)

            await asyncio.to_thread(compact_fenced)
        else:
            await asyncio.to_thread(cleanup_staging, config)
    except Exception:
        logging.exception("maintenance operation %s failed", kind)
    finally:
        heartbeat_stop.set()
        await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            entry = await leases.get("global")
            current = MaintenanceLease.model_validate_json(entry.value)
            if current.owner == worker_id and current.operation_id == lease.operation_id:
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
    leases = await ensure_maintenance_storage(client.jetstream())
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
            get_int("ATLAS_MAINTENANCE_WORKER_METRICS_PORT"),
            addr=get_str("ATLAS_METRICS_HOST"),
        )

    async def heartbeat() -> None:
        while not stop.is_set():
            monitor.heartbeat()
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        while not stop.is_set():
            await _run_operation(
                kind="compact", worker_id=worker_id, leases=leases, config=config
            )
            await _run_operation(
                kind="cleanup", worker_id=worker_id, leases=leases, config=config
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=config.interval_seconds)
            except TimeoutError:
                pass
    finally:
        stop.set()
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        await asyncio.to_thread(health_server.shutdown)
        health_server.server_close()
        if metrics_server is not None:
            await asyncio.to_thread(metrics_server.shutdown)
            metrics_server.server_close()
        await client.drain()


def main() -> None:
    argparse.ArgumentParser(description="Run the Atlas maintenance worker.").parse_args()
    logging.basicConfig(
        level=get_str("ATLAS_LOG_LEVEL"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
