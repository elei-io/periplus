import asyncio
from fnmatch import fnmatch
from urllib.parse import urldefrag, urljoin, urlparse
from uuid import UUID

from sqlalchemy.orm import Session

from actions.crawl.schemas import CrawlPage
from actions.crawl.service import crawl as crawl_service
from actions.shared.progress import ProgressEvent, ProgressReporter, emit_progress

from .schemas import IndexLink


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
        parsed_url.scheme == parsed_start.scheme
        and parsed_url.netloc == parsed_start.netloc
    )


def _link_to_index_link(
    link: dict,
    source_url: str,
    start_url: str,
    depth: int,
    link_index: int,
) -> IndexLink | None:
    href = link.get("href")
    if not href:
        return None

    url = _normalize_url(href, source_url)
    if not _is_crawlable_url(url):
        return None

    return IndexLink(
        source_url=source_url,
        url=url,
        text=link.get("text") or "",
        title=link.get("title") or "",
        depth=depth,
        link_index=link_index,
        internal=_is_same_origin(url, start_url),
    )


def _dedupe_by_url_lowest_depth(links: list[IndexLink]) -> list[IndexLink]:
    deduped: dict[str, IndexLink] = {}
    for link in links:
        existing = deduped.get(link.url)
        if existing is None or link.depth < existing.depth:
            deduped[link.url] = link

    return list(deduped.values())


def _matches_patterns(url: str, patterns: list[str]) -> bool:
    return any(fnmatch(url, pattern) for pattern in patterns)


def _matches_filter(url: str, include: list[str], exclude: list[str]) -> bool:
    if include and not _matches_patterns(url, include):
        return False

    return not _matches_patterns(url, exclude)


def _filter_results(
    links: list[IndexLink],
    include_result: list[str],
    exclude_result: list[str],
) -> list[IndexLink]:
    return [
        link
        for link in links
        if _matches_filter(link.url, include=include_result, exclude=exclude_result)
    ]


def _links_from_page(page: CrawlPage) -> list[dict]:
    if not page.crawl:
        return []

    links = page.crawl.get("links", {})
    return links.get("internal", []) + links.get("external", [])


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
) -> list[IndexLink]:
    start_url = _normalize_url(url, url)
    if max_depth < 0 or not _is_crawlable_url(start_url):
        return []

    include_crawl_patterns = include_crawl or []
    exclude_crawl_patterns = exclude_crawl or []
    include_result_patterns = include_result or []
    exclude_result_patterns = exclude_result or []
    results: list[IndexLink] = []
    seen_results: set[tuple[str, str]] = set()
    visited_pages: set[str] = set()
    scheduled_pages: set[str] = {start_url}
    frontier = [start_url]

    for depth in range(max_depth + 1):
        page_urls = [page_url for page_url in frontier if page_url not in visited_pages]
        if not page_urls:
            break

        operation_id = f"{task_run_id or 'index'}:depth:{depth}"
        await emit_progress(
            progress_reporter,
            ProgressEvent(
                operation_id=operation_id,
                phase="index_depth",
                status="started",
                resource=start_url,
                current=depth + 1,
                total=max_depth + 1,
                message=f"Indexing depth {depth} with {len(page_urls)} pages.",
                metadata={"depth": depth, "pages": len(page_urls)},
            ),
        )

        crawl_output = await crawl_service(
            urls=page_urls,
            progress_reporter=progress_reporter,
            session=session,
            task_run_id=task_run_id,
        )
        next_frontier: list[str] = []

        for page_url, page in zip(page_urls, crawl_output.pages, strict=False):
            visited_pages.add(page_url)
            if not page.success:
                continue

            for link_index, link in enumerate(_links_from_page(page)):
                index_link = _link_to_index_link(
                    link=link,
                    source_url=page_url,
                    start_url=start_url,
                    depth=depth,
                    link_index=link_index,
                )
                if index_link is None:
                    continue

                result_key = (index_link.source_url, index_link.url)
                if result_key not in seen_results:
                    seen_results.add(result_key)
                    results.append(index_link)

                if (
                    index_link.internal
                    and depth < max_depth
                    and index_link.url not in scheduled_pages
                    and _matches_filter(
                        index_link.url,
                        include=include_crawl_patterns,
                        exclude=exclude_crawl_patterns,
                    )
                ):
                    scheduled_pages.add(index_link.url)
                    next_frontier.append(index_link.url)

        frontier = next_frontier
        await emit_progress(
            progress_reporter,
            ProgressEvent(
                operation_id=operation_id,
                phase="index_depth",
                status="succeeded",
                resource=start_url,
                current=depth + 1,
                total=max_depth + 1,
                message=f"Depth {depth} complete; {len(results)} links discovered.",
                metadata={
                    "depth": depth,
                    "discovered_links": len(results),
                    "next_pages": len(next_frontier),
                },
            ),
        )

    filtered_results = _filter_results(
        links=results,
        include_result=include_result_patterns,
        exclude_result=exclude_result_patterns,
    )

    if dedupe:
        return _dedupe_by_url_lowest_depth(filtered_results)

    return filtered_results


def index_sync(
    url: str,
    max_depth: int = 1,
    dedupe: bool = False,
    include_crawl: list[str] | None = None,
    exclude_crawl: list[str] | None = None,
    include_result: list[str] | None = None,
    exclude_result: list[str] | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> list[IndexLink]:
    return asyncio.run(
        index(
            url=url,
            max_depth=max_depth,
            dedupe=dedupe,
            include_crawl=include_crawl,
            exclude_crawl=exclude_crawl,
            include_result=include_result,
            exclude_result=exclude_result,
            progress_reporter=progress_reporter,
        )
    )
