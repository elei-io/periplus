"""Reclaim Periplus-owned transient objects using current frontier ownership."""

from __future__ import annotations

import asyncio
import time
from periplus.operations.metrics import (
    janitor_passes,
    janitor_last_success,
    janitor_duration,
    raw_removed,
)

from datetime import UTC, datetime, timedelta
from itertools import islice
import logging
from uuid import UUID

from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.objects.readiness import PROBE_PREFIX
from periplus.platform.config import get_float
from periplus.platform.config.performance import (
    NAVIGATION_CLEANUP_BATCH_SIZE,
    FRONTIER_CLEANUP_WINDOW_SECONDS,
    FRONTIER_CLEANUP_RETRY_SECONDS,
)
from periplus.platform.telemetry import event
from periplus.platform.health import HealthMonitor
from periplus.platform.postgres.session import SessionLocal
from periplus.platform.process import run_worker_process
from periplus.platform.execution import bounded_call
from periplus.retention.captures import apply_retirements
from periplus.ingestion.archive import Archive

_NAVIGATION_PREFIX = "runtime/navigation/"


def _navigation_acquisition_id(key: str) -> UUID | None:
    parts = key.split("/")
    if len(parts) != 4 or parts[:2] != ["runtime", "navigation"]:
        return None
    try:
        return UUID(hex=parts[2])
    except ValueError:
        return None


def cleanup_navigation(frontier, objects, iterator=None):
    """Scan a bounded metadata batch, retaining the streaming cursor between passes."""
    iterator = (
        iterator
        if iterator is not None
        else iter(objects.list_objects(_NAVIGATION_PREFIX))
    )
    candidates = tuple(islice(iterator, NAVIGATION_CLEANUP_BATCH_SIZE))
    now = datetime.now(UTC)
    cutoff = now - timedelta(
        seconds=get_float("PERIPLUS_NAVIGATION_CLEANUP_GRACE_SECONDS")
    )
    # Beyond the maximum 3,600-second capture, cleanup allowance, and service lease.
    orphan_cutoff = now - timedelta(hours=2)
    keys = []
    for item in candidates:
        identity = _navigation_acquisition_id(item.key)
        if identity is not None and frontier.retire_navigation(
            identity,
            item.key,
            modified_at=item.last_modified,
            cutoff=cutoff,
            orphan_cutoff=orphan_cutoff,
        ):
            keys.append(item.key)
    if keys:
        objects.delete_many(tuple(keys))
    return iterator if len(candidates) == NAVIGATION_CLEANUP_BATCH_SIZE else None


def cleanup_probes(objects, iterator=None):
    """Reclaim bounded abandoned probes without touching immutable evidence."""
    iterator = (
        iterator if iterator is not None else iter(objects.list_objects(PROBE_PREFIX))
    )
    candidates = tuple(islice(iterator, NAVIGATION_CLEANUP_BATCH_SIZE))
    cutoff = datetime.now(UTC) - timedelta(hours=2)
    keys = tuple(item.key for item in candidates if item.last_modified < cutoff)
    if keys:
        objects.delete_many(keys)
    return iterator if len(candidates) == NAVIGATION_CLEANUP_BATCH_SIZE else None


async def cleanup_frontier(frontier: FrontierStore, stop: asyncio.Event) -> bool:
    """Drain keyset scans in short transactions; deadlines apply between batches."""
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    deadline = time.monotonic() + FRONTIER_CLEANUP_WINDOW_SECONDS
    batches = collections_removed = acquisitions_removed = 0
    more = True
    collections_done = False
    while not stop.is_set() and time.monotonic() < deadline:

        def reclaim():
            collections = (
                None
                if collections_done
                else frontier.cleanup_collections(cutoff=cutoff)
            )
            acquisitions = frontier.cleanup_acquisitions(cutoff=cutoff)
            return collections, acquisitions

        cleaning = asyncio.create_task(asyncio.to_thread(reclaim))
        try:
            collections, acquisitions = await asyncio.shield(cleaning)
        except asyncio.CancelledError:
            # An in-flight database transaction must finish before shutdown.
            await asyncio.gather(cleaning, return_exceptions=True)
            raise
        batches += 1
        if collections is not None:
            collections_removed += collections.removed
            collections_done = not collections.more
        acquisitions_removed += acquisitions.removed
        more = not collections_done or acquisitions.more
        if not more:
            break
    event(
        "frontier_cleanup",
        batches=batches,
        collections_removed=collections_removed,
        acquisitions_removed=acquisitions_removed,
        more=more,
    )
    return more


async def _run(stop: asyncio.Event, monitor: HealthMonitor) -> None:
    frontier = FrontierStore(SessionLocal)
    await asyncio.to_thread(frontier.validate_installed)
    objects = object_store_from_env()
    from periplus.retention.identities import cleanup_expired_claims
    from periplus.operations.query_history.store import QueryHistoryStore

    iterator = probe_iterator = None
    monitor.dependencies_ready()
    while not stop.is_set():
        more = False
        try:
            iterator = await bounded_call(
                cleanup_navigation, frontier, objects, iterator
            )
            probe_iterator = await bounded_call(cleanup_probes, objects, probe_iterator)
            more = await cleanup_frontier(frontier, stop)
            await bounded_call(cleanup_expired_claims)
            await bounded_call(QueryHistoryStore(SessionLocal).cleanup)
            await bounded_call(apply_retirements, Archive(objects))
            monitor.subsystem_ready("cleanup")
        except Exception:
            logging.exception("Operational cleanup failed")
            monitor.subsystem_unavailable(
                "cleanup", "See janitor logs for the failed operation"
            )
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=FRONTIER_CLEANUP_RETRY_SECONDS
                if more
                else get_float("PERIPLUS_JANITOR_INTERVAL_SECONDS"),
            )
        except TimeoutError:
            pass


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "PERIPLUS_JANITOR_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    await run_worker_process(
        role="janitor",
        monitor=monitor,
        tasks={"janitor": _run(stop, monitor)},
        stop=stop,
    )
