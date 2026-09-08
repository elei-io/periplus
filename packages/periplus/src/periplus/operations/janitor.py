"""Reclaim Periplus-owned transient objects using current frontier ownership."""
from __future__ import annotations

import asyncio
import time
from periplus.operations.metrics import janitor_passes, janitor_last_success, janitor_duration, raw_removed

from datetime import UTC, datetime, timedelta
from itertools import islice
import logging
from uuid import UUID

from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.objects.readiness import PROBE_PREFIX
from periplus.platform.config import get_float
from periplus.platform.config.performance import NAVIGATION_CLEANUP_BATCH_SIZE
from periplus.platform.health import HealthMonitor
from periplus.platform.postgres.session import SessionLocal
from periplus.platform.process import run_worker_process
from periplus.retention.runtime import RetentionSettings, RetentionSweep, reclaim_pass, bounded_call

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
    iterator = iterator if iterator is not None else iter(objects.list_objects(_NAVIGATION_PREFIX))
    candidates = tuple(islice(iterator, NAVIGATION_CLEANUP_BATCH_SIZE))
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=get_float("PERIPLUS_NAVIGATION_CLEANUP_GRACE_SECONDS"))
    # Beyond the maximum 3,600-second capture, cleanup allowance, and service lease.
    orphan_cutoff = now - timedelta(hours=2)
    keys = []
    for item in candidates:
        identity = _navigation_acquisition_id(item.key)
        if identity is not None and frontier.retire_navigation(identity, item.key,
                modified_at=item.last_modified, cutoff=cutoff, orphan_cutoff=orphan_cutoff):
            keys.append(item.key)
    if keys:
        objects.delete_many(tuple(keys))
    return iterator if len(candidates) == NAVIGATION_CLEANUP_BATCH_SIZE else None


def cleanup_probes(objects, iterator=None):
    """Reclaim bounded abandoned probes without touching immutable evidence."""
    iterator = iterator if iterator is not None else iter(objects.list_objects(PROBE_PREFIX))
    candidates = tuple(islice(iterator, NAVIGATION_CLEANUP_BATCH_SIZE))
    cutoff = datetime.now(UTC) - timedelta(hours=2)
    keys = tuple(item.key for item in candidates if item.last_modified < cutoff)
    if keys:
        objects.delete_many(keys)
    return iterator if len(candidates) == NAVIGATION_CLEANUP_BATCH_SIZE else None


async def _run(stop: asyncio.Event, monitor: HealthMonitor) -> None:
    frontier = FrontierStore(SessionLocal)
    await asyncio.to_thread(frontier.validate_installed)
    objects = object_store_from_env()
    from periplus.platform.messaging.client import connect_nats
    from periplus.platform.messaging.leases import ensure_operation_lease_storage
    from periplus.retention.publications import cleanup_publications
    settings = RetentionSettings.from_env()
    client = await connect_nats() if settings.mode == "purge" else None
    try:
        leases = await ensure_operation_lease_storage(client.jetstream()) if client else None
        retention = RetentionSweep(settings, SessionLocal)
        iterator = probe_iterator = None
        publication_iterator = None
        object_after = None
        monitor.dependencies_ready()
        monitor.subsystem_ready("navigation_retention")
        monitor.subsystem_ready("frontier_retention")
        while not stop.is_set():
            # Cancellation must not leave an unobserved deletion thread running while
            # shutdown reports completion. The object client owns bounded I/O timeouts.
            def cleanup():
                return (cleanup_navigation(frontier, objects, iterator),
                        cleanup_probes(objects, probe_iterator))
            phase_started = time.monotonic()
            processing = asyncio.create_task(asyncio.to_thread(cleanup))
            try:
                iterator, probe_iterator = await asyncio.shield(processing)
            except asyncio.CancelledError:
                await asyncio.gather(processing, return_exceptions=True)
                raise
            except Exception as exc:
                iterator = probe_iterator = None
                janitor_passes.labels("navigation_retention", "failed").inc()
                logging.exception("Periplus navigation retention cleanup failed")
                monitor.subsystem_unavailable("navigation_retention", str(exc) or type(exc).__name__)
            else:
                janitor_passes.labels("navigation_retention", "success").inc()
                janitor_last_success.labels("navigation_retention").set_to_current_time()
                monitor.subsystem_ready("navigation_retention")
            try:
                # Immutable history serves retired collection identities. Retain one
                # hour of current state, also covering the longest reusable-result age.
                cutoff = datetime.now(UTC) - timedelta(hours=1)
                def reclaim():
                    frontier.cleanup_collections(cutoff=cutoff)
                    frontier.cleanup_acquisitions(cutoff=cutoff)
                    from periplus.retention.identities import cleanup_expired_claims
                    cleanup_expired_claims()
                cleaning = asyncio.create_task(asyncio.to_thread(reclaim))
                try:
                    await asyncio.shield(cleaning)
                except asyncio.CancelledError:
                    await asyncio.gather(cleaning, return_exceptions=True)
                    raise
            except Exception as exc:
                janitor_passes.labels("frontier_retention", "failed").inc()
                logging.exception("Periplus frontier retention cleanup failed")
                monitor.subsystem_unavailable("frontier_retention", str(exc) or type(exc).__name__)
            else:
                janitor_passes.labels("frontier_retention", "success").inc()
                janitor_last_success.labels("frontier_retention").set_to_current_time()
                monitor.subsystem_ready("frontier_retention")
            try:
                from periplus.operations.query_history.store import QueryHistoryStore
                await bounded_call(QueryHistoryStore(SessionLocal).cleanup)
            except Exception:
                janitor_passes.labels("query_history", "failed").inc()
                monitor.subsystem_unavailable("query_history", "History cleanup failed")
                logging.exception("Query history cleanup failed")
            else:
                janitor_passes.labels("query_history", "success").inc()
                janitor_last_success.labels("query_history").set_to_current_time()
                monitor.subsystem_ready("query_history")
            if retention.settings.mode != "disabled":
                processing = asyncio.create_task(bounded_call(retention.run))
                try:
                    await asyncio.shield(processing)
                    publication_iterator = await cleanup_publications(settings, SessionLocal, objects, leases, publication_iterator)
                    removed, object_after = await reclaim_pass(settings, objects, leases, object_after)
                    raw_removed.inc(removed)
                except asyncio.CancelledError:
                    await asyncio.gather(processing, return_exceptions=True)
                    raise
                except Exception as exc:
                    janitor_passes.labels("lake_retention", "failed").inc()
                    logging.exception("Lake retention failed; no physical cleanup authorized")
                    monitor.subsystem_unavailable("lake_retention", str(exc))
                else:
                    janitor_passes.labels("lake_retention", "success").inc()
                    janitor_last_success.labels("lake_retention").set_to_current_time()
                    monitor.subsystem_ready("lake_retention")
            janitor_duration.observe(time.monotonic() - phase_started)
            monitor.heartbeat()
            try:
                await asyncio.wait_for(stop.wait(), timeout=get_float("PERIPLUS_JANITOR_INTERVAL_SECONDS"))
            except TimeoutError:
                pass
    finally:
        if client is not None:
            await client.drain()


async def run() -> None:
    stop = asyncio.Event()
    monitor = HealthMonitor(heartbeat_timeout_seconds=get_float("PERIPLUS_JANITOR_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"))
    await run_worker_process(role="janitor", monitor=monitor, tasks={"janitor": _run(stop, monitor)}, stop=stop)
