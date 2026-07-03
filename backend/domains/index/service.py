import asyncio
from fnmatch import fnmatch
from urllib.parse import urldefrag, urljoin, urlparse

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    SemaphoreDispatcher,
)

from .models import IndexLink

_DEFAULT_CONCURRENCY = 10


def _normalize_url(url: str, base_url: str) -> str:
    absolute_url = urljoin(base_url, url)
    clean_url, _ = urldefrag(absolute_url)
    return clean_url.rstrip("/") or clean_url


def _is_crawlable_url(url: str) -> bool:
    return urlparse(url).scheme in {"http", "https"}


def _hierarchy_root_path(start_url: str) -> str:
    path = urlparse(start_url).path.rstrip("/")
    return path or "/"


def _is_inside_hierarchy(url: str, start_url: str) -> bool:
    parsed_url = urlparse(url)
    parsed_start = urlparse(start_url)
    if parsed_url.scheme != parsed_start.scheme or parsed_url.netloc != parsed_start.netloc:
        return False

    root_path = _hierarchy_root_path(start_url)
    if root_path == "/":
        return True

    path = parsed_url.path.rstrip("/")
    return path == root_path or path.startswith(f"{root_path}/")


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
        internal=_is_inside_hierarchy(url, start_url),
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


async def index(
    url: str,
    max_depth: int = 3,
    dedupe: bool = False,
    concurrency: int = _DEFAULT_CONCURRENCY,
    include_crawl: list[str] | None = None,
    exclude_crawl: list[str] | None = None,
    include_result: list[str] | None = None,
    exclude_result: list[str] | None = None,
) -> list[IndexLink]:
    start_url = _normalize_url(url, url)
    if max_depth < 0 or concurrency < 1 or not _is_crawlable_url(start_url):
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
    run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, magic=True)
    dispatcher = SemaphoreDispatcher(semaphore_count=concurrency)

    async with AsyncWebCrawler(
        config=BrowserConfig(headless=True, enable_stealth=True),
    ) as crawler:
        for depth in range(max_depth + 1):
            page_urls = [page_url for page_url in frontier if page_url not in visited_pages]
            if not page_urls:
                break

            crawl_results = await crawler.arun_many(
                urls=page_urls,
                config=run_config,
                dispatcher=dispatcher,
            )
            next_frontier: list[str] = []

            for page_url, result in zip(page_urls, crawl_results, strict=False):
                visited_pages.add(page_url)
                if not result.success:
                    continue

                links = result.links.get("internal", []) + result.links.get("external", [])
                for link_index, link in enumerate(links):
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
    max_depth: int = 3,
    dedupe: bool = False,
    concurrency: int = _DEFAULT_CONCURRENCY,
    include_crawl: list[str] | None = None,
    exclude_crawl: list[str] | None = None,
    include_result: list[str] | None = None,
    exclude_result: list[str] | None = None,
) -> list[IndexLink]:
    return asyncio.run(
        index(
            url=url,
            max_depth=max_depth,
            dedupe=dedupe,
            concurrency=concurrency,
            include_crawl=include_crawl,
            exclude_crawl=exclude_crawl,
            include_result=include_result,
            exclude_result=exclude_result,
        )
    )
