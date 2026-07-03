import asyncio
import json
import time
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler
from crawl4ai.models import CrawlResult

from domains.cache import cache_domain, cache_root, service_cache_root
from domains.crawl import (
    CrawlMode,
    CrawlWait,
    browser_config_for_mode,
    crawl_single_url,
    run_config_for_mode,
)
from domains.progress import CrawlProgressCallback, CrawlProgressEvent, emit_crawl_progress
from domains.quality.service import run_quality_checks, warnings_path, write_quality_warnings

from .models import ArtifactFormat, ScrapeArtifact, ScrapeOutput, ScrapePage, ScrapeStats

_MANIFEST_FILE = "manifest.json"
_HTML_FILE = "page.html"
_CRAWL_FILE = "crawl.json"


def _request_hash(url: str, mode: CrawlMode, wait: CrawlWait) -> str:
    payload = {
        "url": url,
        "mode": mode,
        "wait": wait,
    }
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _url_slug(url: str) -> str:
    parsed_url = urlparse(url)
    slug = parsed_url.path.strip("/") or "page"
    return "".join(char if char.isalnum() else "-" for char in slug).strip("-")[:64] or "page"


def _cache_dir_for_url(url: str, mode: CrawlMode, wait: CrawlWait) -> Path:
    return service_cache_root(cache_domain(url), "scrape") / f"{_url_slug(url)}-{_request_hash(url, mode, wait)}"


def _manifest_path(cache_dir: Path) -> Path:
    return cache_dir / _MANIFEST_FILE


def _artifact_from_file(path: Path, artifact_format: ArtifactFormat) -> ScrapeArtifact | None:
    if not path.is_file():
        return None

    bytes_written = path.stat().st_size
    if bytes_written < 1:
        return None

    return ScrapeArtifact(format=artifact_format, path=str(path), bytes=bytes_written)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _crawl_payload(result: CrawlResult, html_path: Path) -> dict[str, Any]:
    return _json_safe(
        {
            "url": result.url,
            "success": result.success,
            "status_code": result.status_code,
            "redirected_url": result.redirected_url,
            "redirected_status_code": result.redirected_status_code,
            "html_path": str(html_path),
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


def _write_text_artifact(path: Path, value: str, artifact_format: ArtifactFormat) -> ScrapeArtifact:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return ScrapeArtifact(format=artifact_format, path=str(path), bytes=path.stat().st_size)


def _write_json_artifact(path: Path, value: dict[str, Any], artifact_format: ArtifactFormat) -> ScrapeArtifact:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    return ScrapeArtifact(format=artifact_format, path=str(path), bytes=path.stat().st_size)


def _cached_page(url: str, cache_dir: Path, mode: CrawlMode, wait: CrawlWait) -> ScrapePage | None:
    manifest_path = _manifest_path(cache_dir)
    if not manifest_path.is_file():
        return None

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if manifest.get("request") != {"url": url, "mode": mode, "wait": wait}:
        return None

    html_path = cache_dir / _HTML_FILE
    crawl_path = cache_dir / _CRAWL_FILE
    warning_path = warnings_path(cache_dir)
    html_artifact = _artifact_from_file(html_path, "html")
    crawl_artifact = _artifact_from_file(crawl_path, "crawl")
    if html_artifact is None or crawl_artifact is None:
        return None

    warnings = _cached_warnings(cache_dir)
    if not warning_path.is_file():
        try:
            html = html_path.read_text(encoding="utf-8")
            crawl = json.loads(crawl_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            html = ""
            crawl = None

        warnings = run_quality_checks(url=manifest.get("url", url), html=html, crawl=crawl)
        warning_path = write_quality_warnings(cache_dir, warnings)

    return ScrapePage(
        url=manifest.get("url", url),
        success=True,
        cached=True,
        status_code=manifest.get("status_code"),
        duration_seconds=0.0,
        cache_dir=str(cache_dir),
        html_path=str(html_path),
        crawl_path=str(crawl_path),
        warnings_path=str(warning_path),
        warnings=warnings,
        artifacts=[html_artifact, crawl_artifact],
    )


def _cached_warnings(cache_dir: Path):
    path = warnings_path(cache_dir)
    if not path.is_file():
        return []

    try:
        from domains.quality.models import QualityWarning

        return [QualityWarning.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, json.JSONDecodeError, ValueError):
        return []


def _write_manifest(cache_dir: Path, url: str, mode: CrawlMode, wait: CrawlWait, page: ScrapePage) -> None:
    manifest = {
        "request": {
            "url": url,
            "mode": mode,
            "wait": wait,
        },
        "url": page.url,
        "status_code": page.status_code,
        "artifacts": [artifact.model_dump() for artifact in page.artifacts],
    }
    path = _manifest_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


async def _scrape_url(
    crawler: AsyncWebCrawler,
    url: str,
    mode: CrawlMode,
    wait: CrawlWait,
    progress_callback: CrawlProgressCallback | None,
) -> ScrapePage:
    cache_dir = _cache_dir_for_url(url=url, mode=mode, wait=wait)
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
        html_path = cache_dir / _HTML_FILE
        crawl_path = cache_dir / _CRAWL_FILE
        html_artifact = _write_text_artifact(html_path, result.html or "", "html")
        crawl_payload = _crawl_payload(result, html_path)
        crawl_artifact = _write_json_artifact(crawl_path, crawl_payload, "crawl")
        warnings = run_quality_checks(url=result.url, html=result.html or "", crawl=crawl_payload)
        warning_path = write_quality_warnings(cache_dir, warnings)
        artifacts = [html_artifact, crawl_artifact]
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
            cache_dir=str(cache_dir),
            artifacts=[],
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
        cached=False,
        status_code=result.status_code,
        duration_seconds=duration,
        cache_dir=str(cache_dir),
        html_path=str(html_path),
        crawl_path=str(crawl_path),
        warnings_path=str(warning_path),
        warnings=warnings,
        artifacts=artifacts,
        error=result.error_message,
    )


async def _scrape_miss(
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
        if page.success:
            _write_manifest(
                cache_dir=Path(page.cache_dir),
                url=url,
                mode=mode,
                wait=wait,
                page=page,
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
    cache_misses: list[tuple[int, str]] = []

    for index, url in enumerate(urls):
        cache_dir = _cache_dir_for_url(url=url, mode=mode, wait=wait)
        cached_page = _cached_page(url=url, cache_dir=cache_dir, mode=mode, wait=wait)
        if cached_page is None:
            cache_misses.append((index, url))
            continue

        pages_by_index[index] = cached_page
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(url=url, label="cache", status="succeeded", duration=0.0),
        )

    if cache_misses:
        semaphore = asyncio.Semaphore(max(1, concurrency))
        async with AsyncWebCrawler(config=browser_config_for_mode(mode)) as crawler:
            tasks = [
                asyncio.create_task(
                    _scrape_miss(
                        crawler=crawler,
                        index=index,
                        url=url,
                        mode=mode,
                        wait=wait,
                        semaphore=semaphore,
                        progress_callback=progress_callback,
                    )
                )
                for index, url in cache_misses
            ]
            for task in asyncio.as_completed(tasks):
                index, page = await task
                pages_by_index[index] = page

    pages = [pages_by_index[index] for index in range(len(urls))]
    artifacts = [artifact for page in pages for artifact in page.artifacts]
    return ScrapeOutput(
        cache_root=str(cache_root()),
        stats=ScrapeStats(
            requested_urls=len(urls),
            succeeded=sum(1 for page in pages if page.success),
            failed=sum(1 for page in pages if not page.success),
            cache_hits=sum(1 for page in pages if page.cached),
            artifacts=len(artifacts),
            bytes_written=sum(artifact.bytes for artifact in artifacts),
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
