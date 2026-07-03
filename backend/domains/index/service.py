import asyncio
import time
from fnmatch import fnmatch
from hashlib import sha1
from typing import Literal
from urllib.parse import urldefrag, urljoin, urlparse

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
)
from crawl4ai.models import CrawlResult

from .models import IndexLink
from ..progress import CrawlProgressCallback, CrawlProgressEvent, emit_crawl_progress

_DEFAULT_CONCURRENCY = 10
IndexMode = Literal["static", "dynamic", "app"]
IndexWait = Literal["none", "stable", "network", "fixed"]
_FIXED_WAIT_SECONDS = 10.0


def _normalize_url(url: str, base_url: str) -> str:
    absolute_url = urljoin(base_url, url)
    clean_url, _ = urldefrag(absolute_url)
    return clean_url.rstrip("/") or clean_url


def _is_crawlable_url(url: str) -> bool:
    return urlparse(url).scheme in {"http", "https"}


def _is_same_origin(url: str, start_url: str) -> bool:
    parsed_url = urlparse(url)
    parsed_start = urlparse(start_url)
    return parsed_url.scheme == parsed_start.scheme and parsed_url.netloc == parsed_start.netloc


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


def _stable_wait_config() -> dict[str, str]:
    return {
        "js_code_before_wait": """
window.__atlasStableStartedAt = Date.now();
window.__atlasLastLinkCount = document.links.length;
window.__atlasLastMutationAt = Date.now();
window.__atlasStableObserver?.disconnect?.();
window.__atlasStableObserver = new MutationObserver(() => {
  window.__atlasLastMutationAt = Date.now();
});
window.__atlasStableObserver.observe(document.documentElement, {
  childList: true,
  subtree: true,
  attributes: true,
});
""",
        "wait_for": """js:() => {
  const count = document.links.length;
  const now = Date.now();
  if (window.__atlasLastLinkCount !== count) {
    window.__atlasLastLinkCount = count;
    window.__atlasLastMutationAt = now;
    return false;
  }
  const pageHasHadTimeToHydrate = now - (window.__atlasStableStartedAt || now) >= 3000;
  const hasUsableLinks = count > 0;
  const hasBeenQuiet = now - (window.__atlasLastMutationAt || now) >= 1500;
  return document.readyState === "complete" && hasBeenQuiet && (hasUsableLinks || pageHasHadTimeToHydrate);
}""",
    }


def _wait_config(wait: IndexWait) -> dict[str, str]:
    if wait == "stable":
        return _stable_wait_config()

    return {}


def _wait_until_for_wait(wait: IndexWait) -> str:
    if wait == "network":
        return "networkidle"

    return "domcontentloaded"


def _run_config_for_mode(mode: IndexMode, wait: IndexWait) -> CrawlerRunConfig:
    wait_config = _wait_config(wait)
    fixed_delay_config = (
        {"delay_before_return_html": _FIXED_WAIT_SECONDS} if wait == "fixed" else {}
    )

    if mode == "static":
        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            magic=True,
            verbose=False,
            wait_until=_wait_until_for_wait(wait),
            **fixed_delay_config,
            **wait_config,
        )

    if mode == "dynamic":
        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            magic=True,
            verbose=False,
            wait_until=_wait_until_for_wait(wait),
            scan_full_page=True,
            scroll_delay=0.5,
            delay_before_return_html=_FIXED_WAIT_SECONDS if wait == "fixed" else 2.0,
            **wait_config,
        )

    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        magic=True,
        verbose=False,
        wait_until=_wait_until_for_wait(wait),
        scan_full_page=True,
        scroll_delay=0.75,
        delay_before_return_html=_FIXED_WAIT_SECONDS if wait == "fixed" else 4.0,
        **wait_config,
    )


def _browser_config_for_mode(mode: IndexMode, live: bool) -> BrowserConfig:
    return BrowserConfig(
        headless=not live,
        enable_stealth=True,
        text_mode=mode == "static",
        light_mode=mode == "static",
        verbose=False,
    )


def _session_id(url: str) -> str:
    return f"atlas-index-{sha1(url.encode()).hexdigest()}"


def _app_pre_scan_wait_config(url: str, wait: IndexWait) -> CrawlerRunConfig:
    wait_config = _wait_config(wait)
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        magic=True,
        verbose=False,
        wait_until=_wait_until_for_wait(wait),
        delay_before_return_html=_FIXED_WAIT_SECONDS if wait == "fixed" else 0.1,
        session_id=_session_id(url),
        **wait_config,
    )


def _app_scan_config(url: str) -> CrawlerRunConfig:
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        magic=True,
        verbose=False,
        js_only=True,
        session_id=_session_id(url),
        scan_full_page=True,
        scroll_delay=0.75,
        delay_before_return_html=4.0,
    )


async def _crawl_single_url(
    crawler: AsyncWebCrawler,
    page_url: str,
    run_config: CrawlerRunConfig,
    mode: IndexMode,
    wait: IndexWait,
) -> CrawlResult:
    if mode == "app" and wait != "none":
        await crawler.arun(
            url=page_url,
            config=_app_pre_scan_wait_config(page_url, wait=wait),
        )
        return await crawler.arun(url=page_url, config=_app_scan_config(page_url))

    return await crawler.arun(url=page_url, config=run_config)


async def _crawl_url_with_progress(
    crawler: AsyncWebCrawler,
    page_url: str,
    depth: int,
    run_config: CrawlerRunConfig,
    mode: IndexMode,
    wait: IndexWait,
    semaphore: asyncio.Semaphore,
    progress_callback: CrawlProgressCallback | None,
) -> tuple[str, CrawlResult]:
    async with semaphore:
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(url=page_url, label=f"depth {depth}", status="started"),
        )
        start_time = time.perf_counter()

        try:
            result = await _crawl_single_url(
                crawler=crawler,
                page_url=page_url,
                run_config=run_config,
                mode=mode,
                wait=wait,
            )
        except Exception as exc:
            duration = time.perf_counter() - start_time
            result = CrawlResult(
                url=page_url,
                html="",
                metadata={},
                success=False,
                error_message=str(exc),
            )
            await emit_crawl_progress(
                progress_callback,
                CrawlProgressEvent(
                    url=page_url,
                    label=f"depth {depth}",
                    status="failed",
                    duration=duration,
                    error=str(exc),
                ),
            )
            return page_url, result

        duration = time.perf_counter() - start_time
        status = "succeeded" if result.success else "failed"
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=page_url,
                label=f"depth {depth}",
                status=status,
                duration=duration,
                error=result.error_message,
            ),
        )
        return page_url, result


async def _crawl_urls(
    crawler: AsyncWebCrawler,
    page_urls: list[str],
    run_config: CrawlerRunConfig,
    concurrency: int,
    mode: IndexMode,
    wait: IndexWait,
    depth: int,
    progress_callback: CrawlProgressCallback | None,
) -> list[CrawlResult]:
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        asyncio.create_task(
            _crawl_url_with_progress(
                crawler=crawler,
                page_url=page_url,
                depth=depth,
                run_config=run_config,
                mode=mode,
                wait=wait,
                semaphore=semaphore,
                progress_callback=progress_callback,
            )
        )
        for page_url in page_urls
    ]
    results_by_url: dict[str, CrawlResult] = {}

    for task in asyncio.as_completed(tasks):
        page_url, result = await task
        results_by_url[page_url] = result

    return [results_by_url[page_url] for page_url in page_urls]


async def index(
    url: str,
    max_depth: int = 3,
    dedupe: bool = False,
    concurrency: int = _DEFAULT_CONCURRENCY,
    mode: IndexMode = "static",
    live: bool = False,
    wait: IndexWait = "none",
    include_crawl: list[str] | None = None,
    exclude_crawl: list[str] | None = None,
    include_result: list[str] | None = None,
    exclude_result: list[str] | None = None,
    progress_callback: CrawlProgressCallback | None = None,
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
    run_config = _run_config_for_mode(mode, wait=wait)

    async with AsyncWebCrawler(config=_browser_config_for_mode(mode, live=live)) as crawler:
        for depth in range(max_depth + 1):
            page_urls = [page_url for page_url in frontier if page_url not in visited_pages]
            if not page_urls:
                break

            crawl_results = await _crawl_urls(
                crawler=crawler,
                page_urls=page_urls,
                run_config=run_config,
                concurrency=concurrency,
                mode=mode,
                wait=wait,
                depth=depth,
                progress_callback=progress_callback,
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
    mode: IndexMode = "static",
    live: bool = False,
    wait: IndexWait = "none",
    include_crawl: list[str] | None = None,
    exclude_crawl: list[str] | None = None,
    include_result: list[str] | None = None,
    exclude_result: list[str] | None = None,
    progress_callback: CrawlProgressCallback | None = None,
) -> list[IndexLink]:
    return asyncio.run(
        index(
            url=url,
            max_depth=max_depth,
            dedupe=dedupe,
            concurrency=concurrency,
            mode=mode,
            live=live,
            wait=wait,
            include_crawl=include_crawl,
            exclude_crawl=exclude_crawl,
            include_result=include_result,
            exclude_result=exclude_result,
            progress_callback=progress_callback,
        )
    )
