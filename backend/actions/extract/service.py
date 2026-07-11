import asyncio
import time
from uuid import UUID

from crawl4ai import JsonCssExtractionStrategy, JsonXPathExtractionStrategy
from sqlalchemy.orm import Session
from config import get_int

from actions.shared.progress import ProgressReporter, ProgressEvent, emit_progress
from actions.shared.quality.service import run_quality_checks
from actions.shared.data_schema.schemas import SchemaType
from actions.shared.data_schema.service import schema as schema_service
from actions.crawl.schemas import CrawlPage
from actions.crawl.service import crawl as crawl_service
from actions.shared.cache import CacheOptions
from actions.shared.query_schema.schemas import QueryParamOutput
from actions.shared.query_schema.service import (
    cached_query_output_from_page,
    persist_query_param_output,
    query_from_page,
)
from control.data_schemas.service import record_data_schema_failure
from runtime.context import commit_task_checkpoint

from .schemas import ExtractOutput, ExtractSource


def _max_extract_attempts() -> int:
    return get_int("ATLAS_EXTRACT_MAX_ATTEMPTS", minimum=0)


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
    if page.quality_warnings or page.error or not page.success:
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
    prompt: str | None = None,
    extract_data: bool = True,
    extract_query_params: bool = True,
    target_json_example: str | None = None,
    schema_type: SchemaType = "css",
    match: str | None = None,
    progress_reporter: ProgressReporter | None = None,
    session: Session | None = None,
    task_run_id: UUID | None = None,
    cache: CacheOptions | dict[str, object] | None = None,
) -> ExtractOutput:
    if not extract_data and not extract_query_params:
        return ExtractOutput(url=url, success=False, error="Enable at least one extraction mode.")
    if extract_data and not (prompt or "").strip():
        return ExtractOutput(url=url, success=False, error="Data extraction requires a prompt.")

    await emit_progress(
        progress_reporter,
        ProgressEvent(resource=url, phase="extract", status="started"),
    )
    total_start_time = time.perf_counter()
    try:
        crawl_output = await crawl_service(
            urls=[url],
            progress_reporter=progress_reporter,
            session=session,
            task_run_id=task_run_id,
            cache=cache,
            include_links=False,
        )
    except Exception as exc:
        await emit_progress(
            progress_reporter,
            ProgressEvent(
                resource=url,
                phase="extract",
                status="failed",
                duration=time.perf_counter() - total_start_time,
                error=str(exc),
            ),
        )
        raise

    page = crawl_output.pages[0] if crawl_output.pages else None
    if page is None or not page.success:
        await emit_progress(
            progress_reporter,
            ProgressEvent(
                resource=url,
                phase="extract",
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
        await emit_progress(
            progress_reporter,
            ProgressEvent(
                resource=page.url,
                phase="extract",
                status="failed",
                duration=time.perf_counter() - total_start_time,
                error="Crawl did not produce HTML.",
            ),
        )
        return ExtractOutput(url=page.url, success=False, error="Crawl did not produce HTML.")

    query_params: QueryParamOutput | None = None
    query_task: asyncio.Task[QueryParamOutput] | None = None
    if extract_query_params and extract_data:
        if session is not None:
            query_params = cached_query_output_from_page(
                session=session,
                page_url=page.url,
                html=html,
                crawl_id=page.crawl_id,
                document_id=page.document_id,
            )
            commit_task_checkpoint(session)
        if query_params is None:
            query_task = asyncio.create_task(
                query_from_page(
                    page_url=page.url,
                    html=html,
                    crawl_id=page.crawl_id,
                    document_id=page.document_id,
                    progress_reporter=progress_reporter,
                )
            )

    async def cancel_query_task() -> None:
        if query_task is None or query_task.done():
            return
        query_task.cancel()
        await asyncio.gather(query_task, return_exceptions=True)

    source = None
    results: list[dict] = []
    warnings = []
    last_error: str | None = None
    if extract_data:
        max_replacements = _max_extract_attempts()
        schema_output = None
        for attempt in range(max_replacements + 1):
            if schema_output is None:
                schema_output = await schema_service(
                    url=page.url,
                    prompt=(prompt or "").strip(),
                    target_json_example=target_json_example,
                    schema_type=schema_type,
                    html=html,
                    crawl_id=page.crawl_id,
                    document_id=page.document_id,
                    match=match,
                    progress_reporter=progress_reporter,
                    session=session,
                    task_run_id=task_run_id,
                )

            source = ExtractSource(
                schema_id=schema_output.schema_id,
                schema_type=schema_output.schema_type,
            )
            await emit_progress(
                progress_reporter,
                ProgressEvent(
                    resource=page.url,
                    phase="apply_data_schema",
                    status="started",
                    current=attempt + 1,
                    total=max_replacements + 1,
                    message=f"Applying extraction schema, attempt {attempt + 1} of {max_replacements + 1}.",
                ),
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
                record_data_schema_failure(
                    session,
                    schema_id=schema_uuid,
                    error=last_error,
                    exhausted=exhausted,
                )
                commit_task_checkpoint(session)
            await emit_progress(
                progress_reporter,
                ProgressEvent(
                    resource=page.url,
                    phase="apply_data_schema",
                    status="failed",
                    current=attempt + 1,
                    total=max_replacements + 1,
                    message=(
                        "Schema application failed; no retries remain."
                        if exhausted
                        else "Schema application failed; regenerating the schema."
                    ),
                    duration=time.perf_counter() - apply_start_time,
                    error=last_error,
                ),
            )
            if exhausted or session is None or schema_uuid is None:
                await cancel_query_task()
                await emit_progress(
                    progress_reporter,
                    ProgressEvent(
                        resource=page.url,
                        phase="extract",
                        status="failed",
                        duration=time.perf_counter() - total_start_time,
                        error=last_error,
                    ),
                )
                if not warnings:
                    warnings = run_quality_checks(url=page.url, html=html, extraction_results=[])
                return ExtractOutput(url=page.url, success=False, source=source, warnings=warnings, error=last_error)

            await emit_progress(
                progress_reporter,
                ProgressEvent(
                    resource=page.url,
                    phase="regenerate_data_schema",
                    status="started",
                    current=attempt + 2,
                    total=max_replacements + 1,
                    message=f"Regenerating extraction schema for attempt {attempt + 2}.",
                ),
            )
            regenerate_start_time = time.perf_counter()
            schema_output = await schema_service(
                url=page.url,
                prompt=(prompt or "").strip(),
                target_json_example=target_json_example,
                schema_type=schema_type,
                html=html,
                crawl_id=page.crawl_id,
                document_id=page.document_id,
                match=match,
                progress_reporter=progress_reporter,
                session=session,
                task_run_id=task_run_id,
                reuse_existing=False,
                replace_schema_id=schema_uuid,
            )
            warnings = []
            await emit_progress(
                progress_reporter,
                ProgressEvent(
                    resource=page.url,
                    phase="regenerate_data_schema",
                    status="succeeded",
                    current=attempt + 2,
                    total=max_replacements + 1,
                    message="Replacement extraction schema generated.",
                    duration=time.perf_counter() - regenerate_start_time,
                ),
            )
        else:
            await cancel_query_task()
            warnings = run_quality_checks(url=page.url, html=html, extraction_results=[])
            return ExtractOutput(url=page.url, success=False, source=source, warnings=warnings, error=last_error)

        await emit_progress(
            progress_reporter,
            ProgressEvent(
                resource=page.url,
                phase="apply_data_schema",
                status="succeeded",
                current=attempt + 1,
                total=max_replacements + 1,
                message=f"Extracted {len(results)} records.",
                metadata={"records": len(results), "warnings": len(warnings)},
                duration=time.perf_counter() - apply_start_time,
            ),
        )

    if extract_query_params:
        if query_task is not None:
            try:
                query_params = await query_task
            except Exception:
                query_params = QueryParamOutput(
                    url=page.url,
                    crawl_id=str(page.crawl_id) if page.crawl_id else None,
                    document_id=page.document_id,
                    warnings=["Query parameter extraction unavailable."],
                )
            if session is not None and query_params.query_schema is not None:
                persist_query_param_output(
                    session,
                    output=query_params,
                    task_run_id=task_run_id,
                    crawl_id=page.crawl_id,
                    document_id=page.document_id,
                )
                commit_task_checkpoint(session)
        elif query_params is None:
            query_params = await query_from_page(
                page_url=page.url,
                html=html,
                crawl_id=page.crawl_id,
                document_id=page.document_id,
                progress_reporter=progress_reporter,
                session=session,
                task_run_id=task_run_id,
            )

    await emit_progress(
        progress_reporter,
        ProgressEvent(
            resource=page.url,
            phase="extract",
            status="succeeded",
            duration=time.perf_counter() - total_start_time,
        ),
    )
    return ExtractOutput(
        url=page.url,
        success=True,
        source=source,
        results=results,
        query_params=query_params,
        warnings=warnings,
    )


def extract_sync(
    url: str,
    prompt: str | None = None,
    extract_data: bool = True,
    extract_query_params: bool = True,
    target_json_example: str | None = None,
    schema_type: SchemaType = "css",
    match: str | None = None,
    progress_reporter: ProgressReporter | None = None,
    cache: CacheOptions | dict[str, object] | None = None,
) -> ExtractOutput:
    return asyncio.run(
        extract(
            url=url,
            prompt=prompt,
            extract_data=extract_data,
            extract_query_params=extract_query_params,
            target_json_example=target_json_example,
            schema_type=schema_type,
            match=match,
            progress_reporter=progress_reporter,
            cache=cache,
        )
    )
