"""Bounded public navigation exploration, independent of collection completion."""
import asyncio
from collections import defaultdict
from collections.abc import Callable
import json
import logging
from urllib.parse import urlsplit

from periplus.crawl.runtime.background_policy import background_rejection
from periplus.crawl.runtime.background_seen import SeenCandidates, lookup_seen
from periplus.crawl.runtime.frontier_selection import PolicyResolver
from periplus.crawl.runtime.frontier_store import AdmissionDeferred, BackgroundWork, FrontierStore, StaleDispatch
from periplus.crawl.runtime.navigation import load_navigation_package
from periplus.crawl.runtime.navigation_contract import NavigationPackage
from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
from periplus.crawl.runtime.selection_sql import select_links
from periplus.ingestion.objects.store import ObjectStore
from periplus.query.service import QueryRequest

logger = logging.getLogger(__name__)


def background_candidates(navigation: bytes) -> SeenCandidates | None:
    # The navigation package and SQL executor already impose input/memory/time
    # bounds. This first policy considers at most 1,000 distinct links per page.
    urls = select_links("SELECT DISTINCT target_url AS url FROM nav.links ORDER BY url LIMIT 1000", navigation)
    domains = defaultdict(list)
    for url in urls:
        if background_rejection(url) is None:
            host = urlsplit(url).hostname
            if len(domains[host]) < 4:
                domains[host].append(url)
    selected = []
    size = 2
    # Round-robin hosts prevents one page's own domain exhausting its batch.
    for depth in range(4):
        for host in sorted(domains):
            if depth < len(domains[host]):
                url = domains[host][depth]
                # JSON escaping can enlarge the UTF-8 URL representation.
                cost = len(json.dumps(url).encode()) + 2
                if len(selected) < 64 and size + cost <= 64 * 1024:
                    selected.append(url)
                    size += cost
    return SeenCandidates(urls=tuple(selected)) if selected else None


def service_background(store: FrontierStore, work: BackgroundWork, policy: PolicyResolver,
                       objects: ObjectStore, select: Callable[[QueryRequest], SelectionCheckpoint]) -> bool:
    check = store.resume_background_check(work)
    if check is None:
        parent = store.get_acquisition(work.acquisition_id)
        if parent is None or parent.visibility != "public" or parent.navigation is None:
            raise StaleDispatch("background navigation is no longer available")
        candidates = background_candidates(load_navigation_package(objects, NavigationPackage.model_validate(parent.navigation)))
        if candidates is None:
            return True
        check = store.start_background_check(parent.id, candidates)
    if check.result is None:
        check = store.finish_background_check(check, lookup_seen(check.candidates, select))
    for url in check.candidates.urls:
        try:
            store.admit_background(check, url, policy(url))
        except AdmissionDeferred:
            return False
    return True


async def run_background_selection(store: FrontierStore, policy: PolicyResolver, objects: ObjectStore, *,
                                   stop: asyncio.Event,
                                   select: Callable[[QueryRequest], SelectionCheckpoint] | None) -> None:
    while not stop.is_set():
        work = await asyncio.to_thread(store.claim_background) if select is not None else None
        if work is None:
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except TimeoutError:
                pass
            continue
        # The query client, navigation reader, and candidate count are bounded.
        # Drain a cancelled thread before releasing its durable service ownership.
        processing = asyncio.create_task(asyncio.to_thread(service_background, store, work, policy, objects, select))
        try:
            complete = await asyncio.shield(processing)
        except asyncio.CancelledError:
            await asyncio.gather(processing, return_exceptions=True)
            await asyncio.to_thread(store.release_background, work)
            raise
        except (AdmissionDeferred, StaleDispatch):
            await asyncio.to_thread(store.release_background, work)
        except Exception:
            logger.exception("background selection deferred acquisition=%s", work.acquisition_id)
            await asyncio.to_thread(store.release_background, work, error="background_dependency_unavailable")
        else:
            await asyncio.to_thread(store.release_background, work, complete=complete)
