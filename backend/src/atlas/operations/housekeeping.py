"""Periodic cleanup for Atlas-owned transient object-store state."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from atlas.platform.config import get_float, get_int
from atlas.platform.config.performance import NAVIGATION_CLEANUP_BATCH_SIZE
from atlas.platform.health import HealthMonitor
from atlas.ingestion.objects.config import object_store_from_env
from atlas.ingestion.objects.store import ObjectMetadata, ObjectStore
from atlas.crawl.runtime.graph_queue import ensure_graph_storage, get_graph_run
from atlas.platform.messaging.client import connect_nats
from atlas.platform.process import run_worker_process


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
    monitor: HealthMonitor,
) -> None:
    now = datetime.now(UTC)
    terminal_cutoff = now - timedelta(
        seconds=get_float("ATLAS_NAVIGATION_CLEANUP_GRACE_SECONDS")
    )
    orphan_cutoff = now - timedelta(
        seconds=get_int("ATLAS_GRAPH_MAX_RUN_SECONDS")
    )
    try:
        candidates = await asyncio.to_thread(
            _aged_navigation_objects,
            store,
            cutoff=terminal_cutoff,
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
                (
                    states.get(run_id) is None
                    and item.last_modified <= orphan_cutoff
                )
                or (
                    (state := states.get(run_id)) is not None
                    and state.status in terminal
                    and state.completed_at is not None
                    and state.completed_at <= terminal_cutoff
                )
            )
        )
        if keys:
            await asyncio.to_thread(store.delete_many, keys)
        monitor.subsystem_ready("navigation_retention")
    except Exception as exc:
        logging.exception("Atlas navigation retention cleanup failed")
        monitor.subsystem_unavailable(
            "navigation_retention",
            str(exc) or type(exc).__name__,
        )


async def _run(stop: asyncio.Event, monitor: HealthMonitor) -> None:
    client = await connect_nats()
    jetstream = client.jetstream()
    runs, _requests, _workers = await ensure_graph_storage(jetstream)
    object_store = object_store_from_env()
    monitor.dependencies_ready()
    monitor.subsystem_ready("navigation_retention")
    monitor.subsystem_ready("graph_runtime_retention")
    try:
        while not stop.is_set():
            await _cleanup_navigation(
                runs=runs,
                store=object_store,
                monitor=monitor,
            )
            try:
                cutoff = datetime.now(UTC) - timedelta(
                    seconds=get_int(
                        "ATLAS_GRAPH_RUN_RETENTION_SECONDS"
                    )
                )
                await runs.delete_terminal_runs_before(cutoff)
                monitor.subsystem_ready("graph_runtime_retention")
            except Exception as exc:
                logging.exception("Atlas graph runtime retention failed")
                monitor.subsystem_unavailable(
                    "graph_runtime_retention",
                    str(exc) or type(exc).__name__,
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
