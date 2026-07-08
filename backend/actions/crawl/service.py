import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from crawl4ai import AsyncWebCrawler
from crawl4ai.models import CrawlResult
from sqlalchemy.orm import Session

from actions.shared.crawl import (
    CrawlMode,
    CrawlWait,
    browser_config_for_mode,
    crawl_single_url,
    run_config_for_mode,
)
from actions.shared.progress import CrawlProgressCallback, CrawlProgressEvent, emit_crawl_progress
from actions.shared.quality.service import run_quality_checks
from artifacts.models import Artifact
from artifacts.service import task_run_artifacts_dir
from crawls.models import Crawl
from tasks.models import TaskRunArtifact, TaskRunCrawl
from urls.service import resolve_url

from .schemas import CrawlOutput, CrawlPage, CrawlStats


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _input_hash(url: str, mode: CrawlMode, wait: CrawlWait) -> str:
    payload = {"url": url, "mode": mode, "wait": wait}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(value), indent=2, sort_keys=True), encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


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


async def _crawl_url(
    crawler: AsyncWebCrawler,
    url: str,
    mode: CrawlMode,
    wait: CrawlWait,
    progress_callback: CrawlProgressCallback | None,
) -> CrawlPage:
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(url=url, label="crawl", status="started"),
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
            CrawlProgressEvent(url=url, label="crawl", status="failed", duration=duration, error=str(exc)),
        )
        return CrawlPage(
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
            label="crawl",
            status="succeeded" if result.success else "failed",
            duration=duration,
            error=result.error_message,
        ),
    )
    return CrawlPage(
        url=result.url,
        success=result.success,
        status_code=result.status_code,
        duration_seconds=duration,
        html=html,
        crawl=crawl,
        warnings=warnings,
        error=result.error_message,
    )


def _crawl_meta(crawl: dict[str, Any] | None) -> dict[str, Any]:
    if not crawl:
        return {}

    return {
        "metadata": crawl.get("metadata") or {},
        "response_headers": crawl.get("response_headers") or {},
        "downloaded_files": crawl.get("downloaded_files"),
        "js_execution_result": crawl.get("js_execution_result"),
        "session_id": crawl.get("session_id"),
        "network_requests": crawl.get("network_requests"),
        "console_messages": crawl.get("console_messages"),
        "tables": crawl.get("tables"),
        "head_fingerprint": crawl.get("head_fingerprint"),
        "cached_at": crawl.get("cached_at"),
        "cache_status": crawl.get("cache_status"),
        "crawl_stats": crawl.get("crawl_stats"),
    }


def _redirects(crawl: dict[str, Any] | None) -> dict[str, Any]:
    if not crawl:
        return {}

    redirected_url = crawl.get("redirected_url")
    if not redirected_url:
        return {}

    return {
        "redirected_url": redirected_url,
        "redirected_status_code": crawl.get("redirected_status_code"),
    }


def _errors(page: CrawlPage, crawl: dict[str, Any] | None) -> dict[str, Any]:
    error = page.error or (crawl or {}).get("error_message")
    return {"message": error} if error else {}


def _warnings_json(page: CrawlPage) -> dict[str, Any]:
    warnings = [warning.model_dump(mode="json") for warning in page.warnings]
    return {
        "codes": [warning.get("code") for warning in warnings if warning.get("code")],
        "count": len(warnings),
        "warnings": warnings,
    }


def _summary_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []

    items: list[Any] = []
    for child in value.values():
        if isinstance(child, list):
            items.extend(child)
    return items


def _artifact(
    session: Session,
    *,
    crawl: Crawl,
    task_run_id: UUID,
    kind: str,
    path: Path,
    input_hash: str,
    extracted: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    warnings_json: dict[str, Any] | None = None,
) -> Artifact:
    content = path.read_bytes()
    content_type = "text/html" if kind == "html" else "application/json"
    artifact = Artifact(
        crawl_id=crawl.id,
        url_id=crawl.url_id,
        task_run_id=task_run_id,
        kind=kind,
        path=str(path),
        content_type=content_type,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        input_hash=input_hash,
        extracted=extracted or {},
        meta=meta or {},
        warnings_json=warnings_json or {},
    )
    session.add(artifact)
    session.flush()
    session.add(TaskRunArtifact(task_run_id=task_run_id, artifact_id=artifact.id, role="produced"))
    session.flush()
    return artifact


def _persist_page(
    session: Session,
    *,
    task_run_id: UUID,
    index: int,
    requested_url: str,
    page: CrawlPage,
    mode: CrawlMode,
    wait: CrawlWait,
) -> CrawlPage:
    url = resolve_url(session, page.url or requested_url)
    input_hash = _input_hash(url.normalized_url, mode, wait)
    crawl_payload = page.crawl or {}
    warnings_json = _warnings_json(page)
    finished_at = _utc_now()
    duration_ms = int(page.duration_seconds * 1000)
    started_at = finished_at - timedelta(milliseconds=duration_ms)

    crawl = Crawl(
        url_id=url.id,
        task_run_id=task_run_id,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        inputs_json={"url": requested_url, "mode": mode, "wait": wait},
        input_hash=input_hash,
        success=page.success,
        status_code=page.status_code,
        redirects_json=_redirects(crawl_payload),
        errors_json=_errors(page, crawl_payload),
        retry_count=0,
        warnings_json=warnings_json,
        meta=_crawl_meta(crawl_payload),
    )
    session.add(crawl)
    session.flush()
    session.add(TaskRunCrawl(task_run_id=task_run_id, crawl_id=crawl.id, role="produced"))

    artifact_ids: list[UUID] = []
    page_dir = task_run_artifacts_dir(task_run_id) / "pages" / f"{index:04d}"
    if page.html is not None:
        html_path = page_dir / "result.html"
        _write_text(html_path, page.html)
        html_artifact = _artifact(
            session,
            crawl=crawl,
            task_run_id=task_run_id,
            kind="html",
            path=html_path,
            input_hash=input_hash,
            extracted={
                "links": _summary_list(crawl_payload.get("links")),
                "media": _summary_list(crawl_payload.get("media")),
            },
            warnings_json=warnings_json,
        )
        artifact_ids.append(html_artifact.id)

    if page.crawl is not None:
        crawl_path = page_dir / "result.json"
        _write_json(crawl_path, page.crawl)
        crawl_artifact = _artifact(
            session,
            crawl=crawl,
            task_run_id=task_run_id,
            kind="crawl.json",
            path=crawl_path,
            input_hash=input_hash,
            meta={"source": "crawl4ai"},
            warnings_json=warnings_json,
        )
        artifact_ids.append(crawl_artifact.id)

    session.flush()
    return page.model_copy(update={"crawl_id": crawl.id, "artifact_ids": artifact_ids})


async def _crawl_one(
    crawler: AsyncWebCrawler,
    index: int,
    url: str,
    mode: CrawlMode,
    wait: CrawlWait,
    semaphore: asyncio.Semaphore,
    progress_callback: CrawlProgressCallback | None,
) -> tuple[int, CrawlPage]:
    async with semaphore:
        page = await _crawl_url(
            crawler=crawler,
            url=url,
            mode=mode,
            wait=wait,
            progress_callback=progress_callback,
        )
        return index, page


async def crawl(
    urls: list[str],
    mode: CrawlMode = "static",
    wait: CrawlWait = "none",
    concurrency: int = 10,
    progress_callback: CrawlProgressCallback | None = None,
    session: Session | None = None,
    task_run_id: UUID | None = None,
) -> CrawlOutput:
    start_time = time.perf_counter()
    pages_by_index: dict[int, CrawlPage] = {}
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async with AsyncWebCrawler(config=browser_config_for_mode(mode)) as crawler:
        tasks = [
            asyncio.create_task(
                _crawl_one(
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
            if session is not None and task_run_id is not None:
                page = _persist_page(
                    session,
                    task_run_id=task_run_id,
                    index=index,
                    requested_url=urls[index],
                    page=page,
                    mode=mode,
                    wait=wait,
                )
            pages_by_index[index] = page

    pages = [pages_by_index[index] for index in range(len(urls))]
    return CrawlOutput(
        stats=CrawlStats(
            requested_urls=len(urls),
            succeeded=sum(1 for page in pages if page.success),
            failed=sum(1 for page in pages if not page.success),
            duration_seconds=time.perf_counter() - start_time,
        ),
        pages=pages,
    )


def crawl_sync(
    urls: list[str],
    mode: CrawlMode = "static",
    wait: CrawlWait = "none",
    concurrency: int = 10,
    progress_callback: CrawlProgressCallback | None = None,
) -> CrawlOutput:
    return asyncio.run(
        crawl(
            urls=urls,
            mode=mode,
            wait=wait,
            concurrency=concurrency,
            progress_callback=progress_callback,
        )
    )
