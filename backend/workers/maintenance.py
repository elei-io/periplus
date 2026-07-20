"""Atlas off-path catalogue maintenance worker."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from config import get_float

from repository.ingestion.health import HealthMonitor
from repository.maintenance import MaintenanceConfig, cleanup_staging, compact
from runtime.nats_client import connect_nats
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
from workers.lifecycle import (
    WorkerEndpointConfig,
    WorkerEndpoints,
    cancel_task,
    install_signal_handlers,
    monitor_heartbeat,
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

    def operation() -> None:
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
                await asyncio.to_thread(operation)
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
    install_signal_handlers(stop)
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
    endpoints = WorkerEndpoints(WorkerEndpointConfig.from_env("maintenance"))
    endpoints.start_health(monitor)
    endpoints.start_metrics()
    heartbeat_task = asyncio.create_task(monitor_heartbeat(monitor, stop))
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
        await cancel_task(heartbeat_task)
        await endpoints.close()
        await client.drain()
