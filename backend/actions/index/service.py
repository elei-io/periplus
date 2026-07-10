"""Disk-backed bounded website traversal."""

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

from .limits import validate_index_budgets
from .schemas import IndexOutput
from .workset import IndexWorkset


def _normalize_url(url: str, base_url: str) -> str:
    absolute_url = urljoin(base_url, url)
    clean_url, _ = urldefrag(absolute_url)
    return clean_url.rstrip("/") or clean_url


def _is_crawlable_url(url: str) -> bool:
    return urlparse(url).scheme in {"http", "https"}


def _is_same_origin(url: str, start_url: str) -> bool:
    parsed_url = urlparse(url)
    parsed_start = urlparse(start_url)
    return (
        parsed_url.scheme.lower(),
        parsed_url.hostname,
        parsed_url.port,
    ) == (
        parsed_start.scheme.lower(),
        parsed_start.hostname,
        parsed_start.port,
    )


def _matches_patterns(url: str, patterns: list[str]) -> bool:
    return any(fnmatch(url, pattern) for pattern in patterns)


def _matches_filter(url: str, include: list[str], exclude: list[str]) -> bool:
    if include and not _matches_patterns(url, include):
        return False
    return not _matches_patterns(url, exclude)


async def index(
    url: str,
    max_depth: int = 1,
    dedupe: bool = False,
    include_crawl: list[str] | None = None,
    exclude_crawl: list[str] | None = None,
    include_result: list[str] | None = None,
    exclude_result: list[str] | None = None,
    progress_reporter: ProgressReporter | None = None,
    session: Session | None = None,
    task_run_id: UUID | None = None,
    cache: CacheOptions | dict | None = None,
    max_pages: int = 100_000,
    max_links: int = 5_000_000,
    max_temp_bytes: int = 10 * 1024 * 1024 * 1024,
) -> IndexOutput:
    validate_index_budgets(
        max_pages=max_pages,
        max_links=max_links,
        max_temp_bytes=max_temp_bytes,
    )
    start_url = _normalize_url(url, url)
    if max_depth < 0 or not _is_crawlable_url(start_url):
        return IndexOutput(pages=0, failed_pages=0, discovered_links=0, result_links=0)

    crawl_include = include_crawl or []
    crawl_exclude = exclude_crawl or []
    result_include = include_result or []
    result_exclude = exclude_result or []
    batch_size = max(1, int(os.getenv("ATLAS_INDEX_PAGE_BATCH_SIZE", "32")))
    temp_directory = os.getenv("ATLAS_INDEX_TEMP_DIRECTORY") or None

    workset = await asyncio.to_thread(
        IndexWorkset,
        max_pages=max_pages,
        max_links=max_links,
        max_temp_bytes=max_temp_bytes,
        temp_directory=temp_directory,
    )
    completed_normally = False
    next_traversal_ordinal = 0
    repository_pipeline: RepositoryPipeline | None = None
    try:
        cache_options = cache if isinstance(cache, CacheOptions) else CacheOptions.model_validate(cache or {})
        if (
            session is not None
            and task_run_id is not None
            and repository_required_for_urls(
                urls=[start_url],
                cache=cache_options,
                session=session,
                task_run_id=task_run_id,
            )
        ):
            repository_pipeline = RepositoryPipeline(repository_ingestor_from_env())
            await repository_pipeline.__aenter__()
        workset_lock = asyncio.Lock()
        await asyncio.to_thread(workset.seed, start_url)
        while batch := await asyncio.to_thread(workset.claim, batch_size):
            batch_ordinals = list(
                range(next_traversal_ordinal, next_traversal_ordinal + len(batch))
            )
            next_traversal_ordinal += len(batch)
            items = {item.url: item for item in batch}

            async def consume(_index: int, requested_url: str, page: CrawlPage) -> None:
                item = items[requested_url]
                link_groups = (page.crawl or {}).get("links") if page.crawl else None
                if link_groups is None and page.html is not None:
                    link_groups = await asyncio.to_thread(
                        links_from_html,
                        page.html,
                        page_url=page.url,
                    )
                raw_links = chain(
                    (link_groups or {}).get("internal", []),
                    (link_groups or {}).get("external", []),
                )

                def edge_rows():
                    for link_index, link in enumerate(raw_links):
                        href = link.get("href")
                        if not href:
                            continue
                        target = _normalize_url(href, page.url or item.url)
                        if not _is_crawlable_url(target):
                            continue
                        internal = _is_same_origin(target, start_url)
                        yield (
                            target,
                            link.get("text") or "",
                            link.get("title") or "",
                            item.depth,
                            link_index,
                            internal,
                            _matches_filter(target, result_include, result_exclude),
                            (
                                internal
                                and item.depth < max_depth
                                and _matches_filter(
                                    target,
                                    crawl_include,
                                    crawl_exclude,
                                )
                            ),
                        )

                async with workset_lock:
                    await asyncio.to_thread(
                        workset.complete_page,
                        item,
                        edges=edge_rows(),
                        succeeded=page.success,
                    )

            completed_pages, total_pages = await asyncio.to_thread(
                lambda: (workset.completed_page_count(), workset.page_count())
            )
            await emit_progress(
                progress_reporter,
                ProgressEvent(
                    operation_id=f"{task_run_id or 'index'}:frontier",
                    phase="index_depth",
                    status="started",
                    resource=start_url,
                    current=completed_pages,
                    total=total_pages,
                    message=f"Indexing {len(batch)} queued pages.",
                ),
            )
            await crawl_service(
                urls=[item.url for item in batch],
                progress_reporter=progress_reporter,
                session=session,
                task_run_id=task_run_id,
                cache=cache,
                page_consumer=consume,
                retain_pages=False,
                include_links=True,
                repository_pipeline=repository_pipeline,
                usage_role="index_traversal",
                usage_ordinals=batch_ordinals,
            )
            completed_pages, total_pages, discovered_links = await asyncio.to_thread(
                lambda: (
                    workset.completed_page_count(),
                    workset.page_count(),
                    workset.edge_count(),
                )
            )
            await emit_progress(
                progress_reporter,
                ProgressEvent(
                    operation_id=f"{task_run_id or 'index'}:frontier",
                    phase="index_depth",
                    status="succeeded",
                    resource=start_url,
                    current=completed_pages,
                    total=total_pages,
                    message=f"Indexed {completed_pages} pages.",
                    metadata={"discovered_links": discovered_links},
                ),
            )

        counts = await asyncio.to_thread(
            lambda: (
                workset.completed_page_count(),
                workset.failed_page_count(),
                workset.edge_count(),
                workset.result_count(dedupe=dedupe),
            )
        )
        completed_normally = True
    finally:
        if repository_pipeline is not None:
            await repository_pipeline.close()
        await asyncio.to_thread(
            workset.close,
            check_budget=completed_normally,
        )
    return IndexOutput(
        pages=counts[0],
        failed_pages=counts[1],
        discovered_links=counts[2],
        result_links=counts[3],
    )


def index_sync(**kwargs: object) -> IndexOutput:
    return asyncio.run(index(**kwargs))
