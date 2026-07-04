import asyncio
import json
import time
from typing import Any

from crawl4ai import AsyncWebCrawler
from crawl4ai.models import CrawlResult

from actions.shared.crawl import (
    CrawlMode,
    CrawlWait,
    browser_config_for_mode,
    crawl_single_url,
    run_config_for_mode,
)
from actions.shared.progress import CrawlProgressCallback, CrawlProgressEvent, emit_crawl_progress
from actions.shared.quality.service import run_quality_checks

from .schemas import ScrapeOutput, ScrapePage, ScrapeStats


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _crawl_payload(result: CrawlResult) -> dict[str, Any]:
    return _json_safe(
        {
            "url": result.url,
            "success": result.success,
            "status_code": result.status_code,
            "redirected_url": result.redirected_url,
            "redirected_status_code": result.redirected_status_code,
            "links": result.links or {},
            "media": result.media or {},
            "metadata": result.metadata or {},
            "response_headers": result.response_headers or {},
            "downloaded_files": result.downloaded_files,
            "js_execution_result": result.js_execution_result,
            "extracted_content": result.extracted_content,
            "error_message": result.error_message,
            "session_id": result.session_id,
            "network_requests": result.network_requests,
            "console_messages": result.console_messages,
            "tables": result.tables,
            "head_fingerprint": result.head_fingerprint,
            "cached_at": result.cached_at,
            "cache_status": result.cache_status,
            "crawl_stats": result.crawl_stats,
        }
    )


async def _scrape_url(
    crawler: AsyncWebCrawler,
    url: str,
    mode: CrawlMode,
    wait: CrawlWait,
    progress_callback: CrawlProgressCallback | None,
) -> ScrapePage:
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(url=url, label="scrape", status="started"),
    )
    start_time = time.perf_counter()

    try:
        result = await crawl_single_url(
            crawler=crawler,
            url=url,
            run_config=run_config_for_mode(mode=mode, wait=wait),
            mode=mode,
            wait=wait,
        )
        html = result.html or ""
        crawl = _crawl_payload(result)
        warnings = run_quality_checks(url=result.url, html=html, crawl=crawl)
    except Exception as exc:
        duration = time.perf_counter() - start_time
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(url=url, label="scrape", status="failed", duration=duration, error=str(exc)),
        )
        return ScrapePage(
            url=url,
            success=False,
            duration_seconds=duration,
            error=str(exc),
        )

    duration = time.perf_counter() - start_time
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(
            url=url,
            label="scrape",
            status="succeeded" if result.success else "failed",
            duration=duration,
            error=result.error_message,
        ),
    )

    return ScrapePage(
        url=result.url,
        success=result.success,
        status_code=result.status_code,
        duration_seconds=duration,
        html=html,
        crawl=crawl,
        warnings=warnings,
        error=result.error_message,
    )


async def _scrape_one(
    crawler: AsyncWebCrawler,
    index: int,
    url: str,
    mode: CrawlMode,
    wait: CrawlWait,
    semaphore: asyncio.Semaphore,
    progress_callback: CrawlProgressCallback | None,
) -> tuple[int, ScrapePage]:
    async with semaphore:
        page = await _scrape_url(
            crawler=crawler,
            url=url,
            mode=mode,
            wait=wait,
            progress_callback=progress_callback,
        )
        return index, page


async def scrape(
    urls: list[str],
    mode: CrawlMode = "static",
    wait: CrawlWait = "none",
    concurrency: int = 10,
    progress_callback: CrawlProgressCallback | None = None,
) -> ScrapeOutput:
    start_time = time.perf_counter()
    pages_by_index: dict[int, ScrapePage] = {}
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async with AsyncWebCrawler(config=browser_config_for_mode(mode)) as crawler:
        tasks = [
            asyncio.create_task(
                _scrape_one(
                    crawler=crawler,
                    index=index,
                    url=url,
                    mode=mode,
                    wait=wait,
                    semaphore=semaphore,
                    progress_callback=progress_callback,
                )
            )
            for index, url in enumerate(urls)
        ]
        for task in asyncio.as_completed(tasks):
            index, page = await task
            pages_by_index[index] = page

    pages = [pages_by_index[index] for index in range(len(urls))]
    return ScrapeOutput(
        stats=ScrapeStats(
            requested_urls=len(urls),
            succeeded=sum(1 for page in pages if page.success),
            failed=sum(1 for page in pages if not page.success),
            duration_seconds=time.perf_counter() - start_time,
        ),
        pages=pages,
    )


def scrape_sync(
    urls: list[str],
    mode: CrawlMode = "static",
    wait: CrawlWait = "none",
    concurrency: int = 10,
    progress_callback: CrawlProgressCallback | None = None,
) -> ScrapeOutput:
    return asyncio.run(
        scrape(
            urls=urls,
            mode=mode,
            wait=wait,
            concurrency=concurrency,
            progress_callback=progress_callback,
        )
    )
