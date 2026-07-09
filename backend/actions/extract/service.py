import asyncio
import os
import time
from uuid import UUID

from crawl4ai import JsonCssExtractionStrategy, JsonXPathExtractionStrategy
from sqlalchemy.orm import Session

from actions.shared.crawl import CrawlMode, CrawlWait
from actions.shared.progress import CrawlProgressCallback, CrawlProgressEvent, emit_crawl_progress
from actions.shared.quality.service import run_quality_checks
from actions.shared.extract_schema.schemas import SchemaType
from actions.shared.extract_schema.service import schema as schema_service
from actions.crawl.schemas import CrawlPage
from actions.crawl.service import crawl as crawl_service
from extract_schemas.service import record_extract_schema_failure

from .schemas import ExtractOutput, ExtractSource


def _max_extract_attempts() -> int:
    raw = os.getenv("MAX_EXTRACT_ATTEMPTS", "2")
    try:
        return max(0, int(raw))
    except ValueError:
        return 2


def _schema_uuid(schema_id: str) -> UUID | None:
    try:
        return UUID(schema_id)
    except ValueError:
        return None


def _strategy_for_schema(schema_type: SchemaType, extraction_schema: dict):
    if schema_type == "xpath":
        return JsonXPathExtractionStrategy(extraction_schema)

    return JsonCssExtractionStrategy(extraction_schema)


def _apply_schema(schema_type: SchemaType, extraction_schema: dict, *, url: str, html: str) -> list[dict]:
    strategy = _strategy_for_schema(schema_type, extraction_schema)
    return strategy.extract(url=url, html_content=html)


def _clean_empty_schema_error(page: CrawlPage, warnings: list) -> str | None:
    if page.warnings or page.error or not page.success:
        return None

    if page.status_code is not None and page.status_code >= 400:
        return None

    crawl = page.crawl or {}
    if crawl.get("success") is False or crawl.get("error_message"):
        return None

    warning_codes = {warning.code for warning in warnings}
    if warning_codes == {"empty_extraction"}:
        return "Schema extracted no records from an otherwise clean crawl."

    return None


async def extract(
    url: str,
    prompt: str,
    target_json_example: str | None = None,
    schema_type: SchemaType = "css",
    mode: CrawlMode = "static",
    wait: CrawlWait = "none",
    match: str | None = None,
    progress_callback: CrawlProgressCallback | None = None,
    session: Session | None = None,
    task_run_id: UUID | None = None,
) -> ExtractOutput:
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(url=url, label="extract", status="started"),
    )
    total_start_time = time.perf_counter()
    try:
        crawl_output = await crawl_service(
            urls=[url],
            mode=mode,
            wait=wait,
            progress_callback=progress_callback,
            session=session,
            task_run_id=task_run_id,
        )
    except Exception as exc:
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=url,
                label="extract",
                status="failed",
                duration=time.perf_counter() - total_start_time,
                error=str(exc),
            ),
        )
        raise

    page = crawl_output.pages[0] if crawl_output.pages else None
    if page is None or not page.success:
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=url,
                label="extract",
                status="failed",
                duration=time.perf_counter() - total_start_time,
                error=page.error if page else "Crawl failed before producing a page.",
            ),
        )
        return ExtractOutput(
            url=url,
            success=False,
            error=page.error if page else "Crawl failed before producing a page.",
        )

    html = page.html
    if html is None:
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=page.url,
                label="extract",
                status="failed",
                duration=time.perf_counter() - total_start_time,
                error="Crawl did not produce HTML.",
            ),
        )
        return ExtractOutput(url=page.url, success=False, error="Crawl did not produce HTML.")

    max_replacements = _max_extract_attempts()
    schema_output = None
    source = None
    last_error: str | None = None
    warnings = []
    for attempt in range(max_replacements + 1):
        if schema_output is None:
            schema_output = await schema_service(
                url=page.url,
                prompt=prompt,
                target_json_example=target_json_example,
                schema_type=schema_type,
                html=html,
                mode=mode,
                wait=wait,
                match=match,
                progress_callback=progress_callback,
                session=session,
                task_run_id=task_run_id,
            )

        source = ExtractSource(
            schema_id=schema_output.schema_id,
            schema_type=schema_output.schema_type,
        )
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(url=page.url, label="apply schema", status="started"),
        )
        apply_start_time = time.perf_counter()
        try:
            results = _apply_schema(schema_output.schema_type, schema_output.extraction_schema, url=page.url, html=html)
            warnings = run_quality_checks(
                url=page.url,
                html=html,
                crawl=page.crawl,
                extraction_results=results,
            )
            last_error = _clean_empty_schema_error(page, warnings)
            if last_error is None:
                break
        except Exception as exc:
            last_error = str(exc)

        exhausted = attempt >= max_replacements
        schema_uuid = _schema_uuid(schema_output.schema_id)
        if session is not None and schema_uuid is not None:
            record_extract_schema_failure(
                session,
                schema_id=schema_uuid,
                error=last_error,
                exhausted=exhausted,
            )
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=page.url,
                label="apply schema",
                status="failed",
                duration=time.perf_counter() - apply_start_time,
                error=last_error,
            ),
        )
        if exhausted or session is None or schema_uuid is None:
            await emit_crawl_progress(
                progress_callback,
                CrawlProgressEvent(
                    url=page.url,
                    label="extract",
                    status="failed",
                    duration=time.perf_counter() - total_start_time,
                    error=last_error,
                ),
            )
            if not warnings:
                warnings = run_quality_checks(url=page.url, html=html, extraction_results=[])
            return ExtractOutput(url=page.url, success=False, source=source, warnings=warnings, error=last_error)

        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(url=page.url, label="regenerate schema", status="started"),
        )
        regenerate_start_time = time.perf_counter()
        schema_output = await schema_service(
            url=page.url,
            prompt=prompt,
            target_json_example=target_json_example,
            schema_type=schema_type,
            html=html,
            mode=mode,
            wait=wait,
            match=match,
            progress_callback=progress_callback,
            session=session,
            task_run_id=task_run_id,
            reuse_existing=False,
            replace_schema_id=schema_uuid,
        )
        warnings = []
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=page.url,
                label="regenerate schema",
                status="succeeded",
                duration=time.perf_counter() - regenerate_start_time,
            ),
        )
    else:
        warnings = run_quality_checks(url=page.url, html=html, extraction_results=[])
        return ExtractOutput(url=page.url, success=False, source=source, warnings=warnings, error=last_error)

    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(
            url=page.url,
            label="apply schema",
            status="succeeded",
            duration=time.perf_counter() - apply_start_time,
        ),
    )
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(
            url=page.url,
            label="extract",
            status="succeeded",
            duration=time.perf_counter() - total_start_time,
        ),
    )
    return ExtractOutput(url=page.url, success=True, source=source, results=results, warnings=warnings)


def extract_sync(
    url: str,
    prompt: str,
    target_json_example: str | None = None,
    schema_type: SchemaType = "css",
    mode: CrawlMode = "static",
    wait: CrawlWait = "none",
    match: str | None = None,
    progress_callback: CrawlProgressCallback | None = None,
) -> ExtractOutput:
    return asyncio.run(
        extract(
            url=url,
            prompt=prompt,
            target_json_example=target_json_example,
            schema_type=schema_type,
            mode=mode,
            wait=wait,
            match=match,
            progress_callback=progress_callback,
        )
    )
