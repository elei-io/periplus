"""Automatically resolve coverage intent and submit an ordinary bounded crawl run."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import httpx
import tldextract

from periplus.crawl.control.coverage_requests.resolver import CoverageResolver, ResolutionFailed, ResolutionUnavailable, public_start_url
from periplus.crawl.control.coverage_requests.store import CoverageRequestStore
from periplus.crawl.control.crawl_graphs.depth_plan import depth_plan_snapshot
from periplus.crawl.runtime.graph_runs import create_graph_run
from periplus.platform.messaging.leases import operation_leases, OperationLeaseLost, OperationLeaseUnavailable

_NAMESPACE = UUID("d984fa29-31bb-478d-8a08-94cdcaaf5bfc")
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())
_TERMINAL = {"completed", "completed_with_errors", "failed", "cancelled"}


def coverage_run_id(request_id: UUID) -> UUID:
    return uuid5(_NAMESPACE, str(request_id))


def coverage_plan(request):
    # Compare with starting sites, not each intermediate page's origin.
    sites = sorted({_EXTRACT(urlsplit(url).hostname).top_domain_under_public_suffix or urlsplit(url).hostname
                    for url in request.resolved_urls})
    conditions = []
    for site in sites:
        literal = site.replace("'", "''")
        conditions.append(f"(target_host = '{literal}' OR ends_with(target_host, '.{literal}'))")
    internal = "(" + " OR ".join(conditions) + ")"
    predicate = {"internal": internal, "external": f"NOT {internal}", "both": "TRUE"}[request.link_scope]
    return depth_plan_snapshot(request.depth, predicate)


async def process_coverage_request(identity, *, store, resolver, runs, requests, progress, jetstream):
    request = await asyncio.to_thread(store.get, identity)
    if request is None or request.status not in {"pending", "resolving", "ongoing"}:
        return
    now = datetime.now(UTC)
    if request.retry_at is not None and request.retry_at.replace(tzinfo=UTC) > now:
        return
    run_id = request.run_id or coverage_run_id(identity)
    try:
        existing = await runs.get_run(run_id)
        if existing is None and request.started_at is not None and request.started_at.replace(tzinfo=UTC) < now - timedelta(hours=2):
            await asyncio.to_thread(store.update, identity, status="failed", completed_at=now, retry_at=None,
                                   error="The collection run is no longer available. Submit a new request to try again.")
            return
        if existing is not None and existing.status in _TERMINAL:
            failed = existing.status in {"failed", "cancelled"} or (existing.request_count > 0 and existing.failed_request_count == existing.request_count)
            await asyncio.to_thread(store.update, identity,
                run_id=run_id, status="failed" if failed else "completed",
                completed_at=existing.completed_at or now, retry_at=None,
                error=("Collection was cancelled." if existing.status == "cancelled" else "Collection failed. Some observations may still be available.") if failed else None)
            return
        if not request.resolved_urls:
            if request.kind == "url":
                urls = [await public_start_url(request.input)]
            else:
                await asyncio.to_thread(store.update, identity, status="resolving", error=None)
                async def checkpoint(state):
                    await asyncio.to_thread(store.update, identity, resolution=state)
                async with asyncio.timeout(180):
                    urls = await resolver.resolve(request.input, request.max_pages, request.resolution, checkpoint)
            await asyncio.to_thread(store.update, identity, resolved_urls=urls, error=None)
            request = await asyncio.to_thread(store.get, identity)
        if request.started_at is None:
            await asyncio.to_thread(store.update, identity, run_id=run_id, started_at=now, status="ongoing")
            request = await asyncio.to_thread(store.get, identity)
        policies = await asyncio.to_thread(store.policies, request.resolved_urls)
        await create_graph_run(
            runs=runs, requests=requests, progress=progress, jetstream=jetstream,
            snapshot=coverage_plan(request), urls=request.resolved_urls,
            policy_resolver=policies.__getitem__, run_id=run_id,
            max_crawls=request.max_pages, max_run_seconds=3600,
            now=request.started_at.replace(tzinfo=UTC),
        )
        await asyncio.to_thread(store.update, identity, status="ongoing", error=None,
                                retry_at=datetime.now(UTC) + timedelta(seconds=5))
    except ResolutionUnavailable as exc:
        await asyncio.to_thread(store.update, identity, status="pending", error=str(exc),
                                retry_at=datetime.now(UTC) + timedelta(seconds=60))
    except ResolutionFailed as exc:
        await asyncio.to_thread(store.update, identity, status="failed", error=str(exc), completed_at=datetime.now(UTC), retry_at=None)
    except Exception:
        logging.exception("Coverage request processing failed: %s", identity)
        attempts = request.attempts + 1
        # Never abandon a run which may already have been committed. Its stable ID is retried.
        dispatched = request.run_id is not None or await runs.get_run(run_id) is not None
        exhausted = not dispatched and attempts >= 3
        await asyncio.to_thread(store.update, identity, attempts=attempts,
            status="failed" if exhausted else "ongoing" if dispatched else "pending",
            error="Source discovery failed after retries. Please try a more specific description or a URL." if exhausted else "Temporarily unavailable; retrying automatically.",
            completed_at=datetime.now(UTC) if exhausted else None,
            retry_at=None if exhausted else datetime.now(UTC) + timedelta(seconds=min(300, 15 * 2 ** min(attempts, 4))))


async def run_coverage_scheduler(stop, *, leases, runs, requests, progress, jetstream):
    store = CoverageRequestStore()
    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=4)) as client:
        resolver = CoverageResolver(client)
        while not stop.is_set():
            try:
                for identity in await asyncio.to_thread(store.candidates, datetime.now(UTC)):
                    if stop.is_set():
                        break
                    try:
                        async with operation_leases(leases, [str(identity)], phase="coverage-request", acquire_timeout=0) as guard:
                            task = asyncio.create_task(process_coverage_request(identity, store=store, resolver=resolver,
                                runs=runs, requests=requests, progress=progress, jetstream=jetstream))
                            lost = asyncio.create_task(guard.wait_lost())
                            try:
                                done, _ = await asyncio.wait([task, lost], return_when=asyncio.FIRST_COMPLETED)
                                if lost in done:
                                    raise OperationLeaseLost("Coverage request lease lost")
                                await task
                            finally:
                                task.cancel(); lost.cancel()
                                await asyncio.gather(task, lost, return_exceptions=True)
                    except (OperationLeaseUnavailable, OperationLeaseLost):
                        continue
            except Exception:
                logging.exception("Coverage scheduler tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass
