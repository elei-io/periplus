"""Small, bounded breadth-first website traversal."""

from __future__ import annotations

import asyncio
import os
from fnmatch import fnmatch
from itertools import chain
from urllib.parse import urldefrag, urljoin, urlparse
from uuid import UUID

from sqlalchemy.orm import Session

from actions.crawl.schemas import CrawlPage
from actions.crawl.service import crawl as crawl_service, repository_required_for_urls
from actions.shared.cache import CacheOptions
from actions.shared.progress import ProgressEvent, ProgressReporter, emit_progress
from dom import links_from_html
from repository import RepositoryPipeline, repository_ingestor_from_env

from .schemas import IndexLink, IndexOutput


def _normalize(url: str, base: str) -> str:
    clean, _ = urldefrag(urljoin(base, url))
    return clean.rstrip("/") or clean


def _same_origin(url: str, start: str) -> bool:
    left, right = urlparse(url), urlparse(start)
    return (left.scheme.lower(), left.hostname, left.port) == (right.scheme.lower(), right.hostname, right.port)


def _included(url: str, include: list[str], exclude: list[str]) -> bool:
    return (not include or any(fnmatch(url, pattern) for pattern in include)) and not any(fnmatch(url, pattern) for pattern in exclude)


async def index(
    url: str, max_depth: int = 1, dedupe: bool = False,
    include_crawl: list[str] | None = None, exclude_crawl: list[str] | None = None,
    include_result: list[str] | None = None, exclude_result: list[str] | None = None,
    progress_reporter: ProgressReporter | None = None, session: Session | None = None,
    task_run_id: UUID | None = None, cache: CacheOptions | dict | None = None,
    max_pages: int = 10_000, max_links: int = 250_000,
) -> IndexOutput:
    start = _normalize(url, url)
    if max_depth < 0 or urlparse(start).scheme not in {"http", "https"}:
        return IndexOutput(pages=0, failed_pages=0, discovered_links=0, result_links=0)

    frontier: dict[str, int] = {start: 0}
    visited: set[str] = set()
    links: list[IndexLink] = []
    failed = discovered = 0
    batch_size = max(1, int(os.getenv("ATLAS_INDEX_PAGE_BATCH_SIZE", "32")))
    pipeline: RepositoryPipeline | None = None
    cache_options = cache if isinstance(cache, CacheOptions) else CacheOptions.model_validate(cache or {})
    if session is not None and task_run_id is not None and repository_required_for_urls(urls=[start], cache=cache_options, session=session, task_run_id=task_run_id):
        pipeline = RepositoryPipeline(repository_ingestor_from_env())
        await pipeline.__aenter__()

    try:
        while pending := [(item, depth) for item, depth in frontier.items() if item not in visited][:batch_size]:
            if len(visited) + len(pending) > max_pages:
                raise ValueError(f"index exceeded its {max_pages} page limit")
            depth_by_url = dict(pending)
            ordinals = list(range(len(visited), len(visited) + len(pending)))

            async def consume(_index: int, requested: str, page: CrawlPage) -> None:
                nonlocal failed, discovered
                visited.add(requested)
                failed += not page.success
                groups = (page.crawl or {}).get("links") if page.crawl else None
                if groups is None and page.html is not None:
                    groups = await asyncio.to_thread(links_from_html, page.html, page_url=page.url)
                for link_index, link in enumerate(chain((groups or {}).get("internal", []), (groups or {}).get("external", []))):
                    href = link.get("href")
                    if not href:
                        continue
                    target = _normalize(href, page.url or requested)
                    if urlparse(target).scheme not in {"http", "https"}:
                        continue
                    discovered += 1
                    if discovered > max_links:
                        raise ValueError(f"index exceeded its {max_links} link limit")
                    internal, depth = _same_origin(target, start), depth_by_url[requested]
                    if _included(target, include_result or [], exclude_result or []):
                        links.append(IndexLink(source_url=requested, url=target, text=link.get("text") or "", title=link.get("title") or "", depth=depth, link_index=link_index, internal=internal))
                    if internal and depth < max_depth and _included(target, include_crawl or [], exclude_crawl or []):
                        frontier.setdefault(target, depth + 1)

            await emit_progress(progress_reporter, ProgressEvent(operation_id=f"{task_run_id or 'index'}:frontier", phase="index_depth", status="started", resource=start, current=len(visited), total=len(frontier), message=f"Indexing {len(pending)} queued pages."))
            await crawl_service(urls=[item for item, _ in pending], progress_reporter=progress_reporter, session=session, task_run_id=task_run_id, cache=cache, page_consumer=consume, retain_pages=False, include_links=True, repository_pipeline=pipeline, usage_role="index_traversal", usage_ordinals=ordinals)
            await emit_progress(progress_reporter, ProgressEvent(operation_id=f"{task_run_id or 'index'}:frontier", phase="index_depth", status="succeeded", resource=start, current=len(visited), total=len(frontier), message=f"Indexed {len(visited)} pages.", metadata={"discovered_links": discovered}))
    finally:
        if pipeline is not None:
            await pipeline.close()

    if dedupe:
        links = list({link.url: link for link in links}.values())
    result_count = len(links)
    limit = max(1, int(os.getenv("ATLAS_INDEX_RESULT_LIMIT", "10000")))
    return IndexOutput(pages=len(visited), failed_pages=failed, discovered_links=discovered, result_links=result_count, links=links[:limit])


def index_sync(**kwargs: object) -> IndexOutput:
    return asyncio.run(index(**kwargs))
