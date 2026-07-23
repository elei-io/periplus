"""Periodic cleanup for Atlas-owned transient object-store state."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from config import get_float, get_int
from config.performance import NAVIGATION_CLEANUP_BATCH_SIZE
from repository.ingestion.health import HealthMonitor
from repository.objects.config import object_store_from_env, staging_root_from_env
from repository.objects.store import ObjectMetadata, ObjectStore
from repository.service import cleanup_staging_files
from runtime.graph_queue import ensure_graph_storage, get_graph_run
from runtime.nats_client import connect_nats
from runtime.operation_leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)
from runtime.resource_governor import (
    DURABLE_RESOURCE_WAIT,
    ResourceCapacityUnavailable,
    ResourceNeed,
    ResourcePermitLost,
    ResourceRequest,
    ensure_resource_governor_storage,
    resource_permits,
)
from workers.lifecycle import run_worker_process


_NAVIGATION_PREFIX = "runtime/navigation/"


def _navigation_run_id(key: str) -> UUID | None:
    parts = key.split("/")
    if len(parts) < 4 or parts[:2] != ["runtime", "navigation"]:
        return None
    try:
        return UUID(hex=parts[2])
    except ValueError:
        return None


def _aged_navigation_objects(
    store: ObjectStore,
    *,
    cutoff: datetime,
    limit: int = NAVIGATION_CLEANUP_BATCH_SIZE,
) -> tuple[ObjectMetadata, ...]:
    aged: list[ObjectMetadata] = []
    for item in store.list_objects(_NAVIGATION_PREFIX):
        if item.last_modified <= cutoff:
            aged.append(item)
            if len(aged) >= limit:
                break
    return tuple(aged)


async def _cleanup_navigation(
    *,
    runs,
    store: ObjectStore,
    leases,
    resources,
    monitor: HealthMonitor,
) -> None:
    cutoff = datetime.now(UTC) - timedelta(
        seconds=get_int("ATLAS_GRAPH_MAX_RUN_SECONDS")
    )
    try:
        async with operation_leases(
            leases,
            ("navigation-retention",),
            phase="housekeeping",
            acquire_timeout=0,
        ):
            async with resource_permits(
                resources,
                ResourceRequest(
                    operation_id="housekeeping:navigation-retention",
                    service_class="maintenance",
                    resources=(
                        ResourceNeed(name="object:read", units=1),
                        ResourceNeed(name="object:write", units=1),
                    ),
                ),
                acquire_timeout=DURABLE_RESOURCE_WAIT,
            ):
                candidates = await asyncio.to_thread(
                    _aged_navigation_objects,
                    store,
                    cutoff=cutoff,
                )
                run_ids = {
                    item.key: _navigation_run_id(item.key) for item in candidates
                }
                unique_ids = {
                    run_id for run_id in run_ids.values() if run_id is not None
                }
                states = dict(
                    zip(
                        unique_ids,
                        await asyncio.gather(
                            *(get_graph_run(runs, run_id) for run_id in unique_ids)
                        ),
                        strict=True,
                    )
                )
                terminal = {
                    "completed",
                    "completed_with_errors",
                    "failed",
                    "cancelled",
                }
                keys = tuple(
                    item.key
                    for item in candidates
                    if (run_id := run_ids[item.key]) is not None
                    and (
                        states.get(run_id) is None
                        or states[run_id].status in terminal
                    )
                )
                if keys:
                    await asyncio.to_thread(store.delete_many, keys)
        monitor.subsystem_ready("navigation_retention")
    except (OperationLeaseUnavailable, ResourceCapacityUnavailable):
        return
    except (OperationLeaseLost, ResourcePermitLost) as exc:
        monitor.subsystem_unavailable("navigation_retention", str(exc))
    except Exception as exc:
        logging.exception("Atlas navigation retention cleanup failed")
        monitor.subsystem_unavailable(
            "navigation_retention",
            str(exc) or type(exc).__name__,
        )


async def _run(stop: asyncio.Event, monitor: HealthMonitor) -> None:
    client = await connect_nats()
    jetstream = client.jetstream()
    leases = await ensure_operation_lease_storage(jetstream)
    resources = await ensure_resource_governor_storage(jetstream)
    runs, _requests, _workers = await ensure_graph_storage(jetstream)
    object_store = object_store_from_env()
    monitor.dependencies_ready()
    monitor.subsystem_ready("staging_retention")
    monitor.subsystem_ready("navigation_retention")
    try:
        while not stop.is_set():
            try:
                await asyncio.to_thread(
                    cleanup_staging_files,
                    staging_root_from_env(),
                    older_than_seconds=get_float(
                        "ATLAS_INGEST_STAGING_GRACE_SECONDS"
                    ),
                )
                monitor.subsystem_ready("staging_retention")
            except Exception as exc:
                logging.exception("Atlas staging cleanup failed")
                monitor.subsystem_unavailable(
                    "staging_retention",
                    str(exc) or type(exc).__name__,
                )
            await _cleanup_navigation(
                runs=runs,
                store=object_store,
                leases=leases,
                resources=resources,
                monitor=monitor,
            )
            monitor.heartbeat()
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=get_float("ATLAS_HOUSEKEEPING_INTERVAL_SECONDS"),
                )
            except TimeoutError:
                pass
    finally:
        await client.drain()


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_HOUSEKEEPING_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    await run_worker_process(
        role="housekeeping",
        monitor=monitor,
        tasks={"housekeeping": _run(stop, monitor)},
        stop=stop,
    )
