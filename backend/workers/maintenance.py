"""Atlas off-path catalogue maintenance worker."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from typing import Literal

from config import get_bool, get_float, get_int, get_str
from prometheus_client import start_http_server

from repository.catalogue.operations import maintenance_lock
from repository.ingestion.health import HealthMonitor, start_health_server
from repository.maintenance import MaintenanceConfig, cleanup_staging, compact
from runtime.graph_queue import connect_nats
from runtime.operation_leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)
from runtime.resource_governor import (
    ResourceCapacityUnavailable,
    ResourceLimits,
    ResourcePermitLost,
    catalogue_request,
    ensure_resource_governor_storage,
    resource_permits,
)


MaintenanceKind = Literal["compact", "cleanup"]


async def _run_operation(
    *,
    kind: MaintenanceKind,
    operation_lease_store,
    resource_grants,
    config: MaintenanceConfig,
    monitor: HealthMonitor | None = None,
) -> None:
    """Run one singleton operation only after all shared pressure has drained."""

    limits = ResourceLimits.from_env()

    def operation_fenced() -> None:
        with maintenance_lock():
            if kind == "compact":
                compact(config)
            else:
                cleanup_staging(config)

    try:
        async with operation_leases(
            operation_lease_store,
            (kind,),
            phase="maintenance",
            acquire_timeout=0,
        ):
            async with resource_permits(
                resource_grants,
                catalogue_request(
                    f"maintenance:{kind}",
                    service_class="maintenance",
                    object_read_units=limits.object_read,
                    object_write_units=limits.object_write,
                    limits=limits,
                    exclusive=True,
                ),
            ):
                await asyncio.to_thread(operation_fenced)
        if monitor is not None:
            monitor.subsystem_ready("maintenance_admission")
    except (
        OperationLeaseUnavailable,
        ResourceCapacityUnavailable,
    ):
        return
    except (OperationLeaseLost, ResourcePermitLost) as exc:
        if monitor is not None:
            monitor.subsystem_unavailable("maintenance_admission", str(exc))
        logging.exception("maintenance %s admission was lost", kind)
    except Exception:
        logging.exception("maintenance operation %s failed", kind)


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for value in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(value, stop.set)
    config = MaintenanceConfig.from_env()
    client = await connect_nats()
    jetstream = client.jetstream()
    operation_lease_store = await ensure_operation_lease_storage(jetstream)
    resource_grants = await ensure_resource_governor_storage(jetstream)
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_MAINTENANCE_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    monitor.dependencies_ready()
    monitor.subsystem_ready("maintenance_admission")
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
                kind="compact",
                operation_lease_store=operation_lease_store,
                resource_grants=resource_grants,
                config=config,
                monitor=monitor,
            )
            await _run_operation(
                kind="cleanup",
                operation_lease_store=operation_lease_store,
                resource_grants=resource_grants,
                config=config,
                monitor=monitor,
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
