"""Bounded crawler loops over one frontier and process-owned delivery handles.

Composition supplies reconciled NATS handles and an open acquisition pipeline.
Polling never provisions infrastructure or opens a lake writer.
"""
import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import logging
import time

from periplus.crawl.acquisition.capture import connect_cdp
from periplus.crawl.acquisition.errors import PlaywrightRuntimeLost

from nats.errors import TimeoutError as NatsTimeoutError

from periplus.query.service import QueryRequest

from periplus.crawl.control.collections.discovery import DiscoveryState, DiscoveryUnavailable, SourceDiscovery
from periplus.crawl.control.collections.schemas import CollectionExecutionSpec, CollectionSpec
from periplus.crawl.runtime.frontier_capture import handle_capture_delivery
from periplus.crawl.runtime.frontier_health import DispatchHealth
from periplus.crawl.runtime.frontier_outbox import run_ingestion_receipts, run_outbox_relay
from periplus.crawl.runtime.frontier_selection import (
    PolicyResolver, process_link_selection, process_seed_selection,
)
from periplus.crawl.runtime.frontier_store import CollectionWork, FrontierStore
from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
from periplus.ingestion.objects.store import ObjectStore

logger = logging.getLogger(__name__)


def service_collection(store: FrontierStore, work: CollectionWork, policy: PolicyResolver,
                       objects: ObjectStore, *,
                       seed_query: Callable[[QueryRequest], SelectionCheckpoint] | None = None) -> float:
    """Do one bounded selection pass and return its resumption delay in seconds."""
    collection = store.get_collection(work.collection_id)
    if collection is None or collection.status == "settled":
        return 0
    spec = CollectionExecutionSpec.model_validate(collection.spec)
    if collection.deadline_at is not None and collection.deadline_at <= datetime.now(UTC):
        store.stop_collection(collection.id, reason="duration_limit")
        return 1
    if collection.status == "paused":
        return 30
    if not collection.seeds_settled:
        result = process_seed_selection(store, collection.id, policy, seed_query=seed_query)
        return 15 if result == "waiting" else 0
    interest = store.next_selection(collection.id)
    if interest is not None:
        result = process_link_selection(store, interest, policy, objects)
        return 15 if result == "waiting" else 0
    store.settle_collection(collection.id)
    return 15


async def _service_collection(store, work, policy, objects, seed_query, discovery) -> float:
    collection = await asyncio.to_thread(store.get_collection, work.collection_id)
    if collection is not None and collection.status == "active":
        spec = CollectionExecutionSpec.model_validate(collection.spec)
        if (spec.seed_description and collection.selection_checkpoint is None
                and (collection.deadline_at is None or collection.deadline_at > datetime.now(UTC))):
            state = DiscoveryState.model_validate(collection.discovery_state or {})
            if not state.complete:
                if discovery is None:
                    raise DiscoveryUnavailable("Source discovery is unavailable.")
                async with asyncio.timeout(30):
                    selected = await discovery.step(spec.seed_description, spec.page_limit, state)
                await asyncio.to_thread(store.checkpoint_discovery, work, state.revision, selected)
                return 0
    return await asyncio.to_thread(service_collection, store, work, policy, objects, seed_query=seed_query)


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_collection_selection(store: FrontierStore, policy: PolicyResolver,
                                   objects: ObjectStore, *, stop: asyncio.Event,
                                   seed_query: Callable[[QueryRequest], SelectionCheckpoint] | None = None,
                       discovery: SourceDiscovery | None = None) -> None:
    while not stop.is_set():
        work = await asyncio.to_thread(store.claim_collection)
        if work is None:
            await _wait(stop, 1)
            continue
        # Cancelling to_thread alone would release ownership while SQL/admission
        # still ran in its thread. Drain the bounded pass before releasing it.
        processing = asyncio.create_task(_service_collection(
            store, work, policy, objects, seed_query, discovery,
        ))
        try:
            delay = await asyncio.shield(processing)
        except asyncio.CancelledError:
            await asyncio.gather(processing, return_exceptions=True)
            await asyncio.to_thread(store.release_collection, work)
            raise
        except DiscoveryUnavailable:
            await asyncio.to_thread(store.release_collection, work, error="discovery_dependency_unavailable")
        except ValueError:
            logger.exception("invalid collection selection collection=%s", work.collection_id)
            await asyncio.to_thread(store.stop_collection, work.collection_id, reason="failed")
            await asyncio.to_thread(store.release_collection, work)
        except Exception:
            logger.exception("collection selection deferred collection=%s", work.collection_id)
            await asyncio.to_thread(store.release_collection, work, error="selection_dependency_unavailable")
        else:
            await asyncio.to_thread(store.release_collection, work, delay_seconds=delay)


async def run_dispatch(store: FrontierStore, *, pipeline, playwright, stop: asyncio.Event, health: DispatchHealth | None = None) -> None:
    health = health or DispatchHealth()
    healthy_until = 0.0
    while not stop.is_set():
        if time.monotonic() >= healthy_until:
            health.checking()
            reason = "ingestion_delivery_unavailable"
            try:
                await pipeline.queue.check_available()
                reason = "storage_unavailable"
                await pipeline.check_storage_available()
                reason = "cdp_unavailable"
                async with connect_cdp(playwright):
                    pass
            except PlaywrightRuntimeLost:
                health.blocked("cdp_unavailable")
                raise
            except Exception:
                health.blocked(reason)
                logger.warning("frontier dispatch waiting for acquisition dependencies", exc_info=True)
                await _wait(stop, 30)
                continue
            health.ready()
            healthy_until = health.valid_until
        if stop.is_set():
            break
        dispatched = await asyncio.to_thread(store.dispatch_next)
        if dispatched is None:
            await _wait(stop, 1)


async def run_recovery(store: FrontierStore, *, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.to_thread(store.reconcile_exclusions)
        expired = await asyncio.to_thread(store.expired_dispatches)
        for identity in expired:
            if stop.is_set():
                break
            await asyncio.to_thread(store.recover_dispatch, identity)
        await _wait(stop, 5)


async def run_capture_lane(subscription, store: FrontierStore, pipeline, playwright, *,
                           operation_bucket, domain_bucket, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            messages = await subscription.fetch(batch=1, timeout=1)
        except (TimeoutError, NatsTimeoutError):
            continue
        for message in messages:
            if stop.is_set():
                await message.nak(delay=1)
                continue
            await handle_capture_delivery(message, store, pipeline, playwright,
                                          operation_bucket=operation_bucket, domain_bucket=domain_bucket)


async def run_schedules(store, *, stop):
    from periplus.crawl.control.schedules.service import ScheduleStore
    from periplus.crawl.runtime.request_schedules import create_due_requests
    schedules = ScheduleStore(store._sessions)
    while not stop.is_set():
        await asyncio.to_thread(create_due_requests, schedules)
        await _wait(stop, 1)


async def run_frontier(store: FrontierStore, *, subscription, jetstream, ingestion,
                       pipeline, playwright, operation_bucket, domain_bucket,
                       policy: PolicyResolver, stop: asyncio.Event, capture_lanes: int,
                       seed_query: Callable[[QueryRequest], SelectionCheckpoint] | None = None,
                       discovery: SourceDiscovery | None = None, dispatch_health: DispatchHealth | None = None) -> None:
    """Run dispatch, selection, recovery, relay, and bounded capture together.

    Unexpected loop failures cancel sibling loops and remote work. Expiring
    claims recover unfinished work on restart. Normal stop drains current work.
    """
    if not 1 <= capture_lanes <= 48:
        raise ValueError("capture lanes must be between one and 48")
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(run_schedules(store, stop=stop), name="request-schedules")
        tasks.create_task(run_dispatch(store, pipeline=pipeline, playwright=playwright, stop=stop, health=dispatch_health), name="frontier-dispatch")
        tasks.create_task(run_recovery(store, stop=stop), name="frontier-recovery")
        tasks.create_task(run_outbox_relay(store, jetstream, ingestion, stop=stop), name="frontier-outbox")
        tasks.create_task(run_ingestion_receipts(store, ingestion, stop=stop), name="frontier-ingestion-receipts")
        tasks.create_task(run_collection_selection(
            store, policy, pipeline.html_repository.store, stop=stop, seed_query=seed_query, discovery=discovery,
        ), name="frontier-selection")
        for lane in range(capture_lanes):
            tasks.create_task(run_capture_lane(
                subscription, store, pipeline, playwright, operation_bucket=operation_bucket,
                domain_bucket=domain_bucket, stop=stop,
            ), name=f"frontier-capture-{lane}")
