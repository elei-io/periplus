import asyncio
import time

from crawl4ai import JsonCssExtractionStrategy, JsonXPathExtractionStrategy

from actions.shared.crawl import CrawlMode, CrawlWait
from actions.shared.progress import CrawlProgressCallback, CrawlProgressEvent, emit_crawl_progress
from actions.shared.quality.service import run_quality_checks
from actions.shared.extract_schema.schemas import SchemaType
from actions.shared.extract_schema.service import schema as schema_service
from actions.scrape.service import scrape as scrape_service

from .schemas import ExtractOutput, ExtractSource


def _strategy_for_schema(schema_type: SchemaType, extraction_schema: dict):
    if schema_type == "xpath":
        return JsonXPathExtractionStrategy(extraction_schema)

    return JsonCssExtractionStrategy(extraction_schema)


async def extract(
    url: str,
    prompt: str,
    target_json_example: str | None = None,
    schema_type: SchemaType = "css",
    mode: CrawlMode = "static",
    wait: CrawlWait = "none",
    progress_callback: CrawlProgressCallback | None = None,
) -> ExtractOutput:
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(url=url, label="extract", status="started"),
    )
    total_start_time = time.perf_counter()
    try:
        scrape_output = await scrape_service(
            urls=[url],
            mode=mode,
            wait=wait,
            progress_callback=progress_callback,
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

    page = scrape_output.pages[0] if scrape_output.pages else None
    if page is None or not page.success:
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=url,
                label="extract",
                status="failed",
                duration=time.perf_counter() - total_start_time,
                error=page.error if page else "Scrape failed before producing a page.",
            ),
        )
        return ExtractOutput(
            url=url,
            success=False,
            error=page.error if page else "Scrape failed before producing a page.",
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
                error="Scrape did not produce HTML.",
            ),
        )
        return ExtractOutput(url=page.url, success=False, error="Scrape did not produce HTML.")

    schema_output = await schema_service(
        url=page.url,
        prompt=prompt,
        target_json_example=target_json_example,
        schema_type=schema_type,
        html=html,
        mode=mode,
        wait=wait,
        progress_callback=progress_callback,
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
        strategy = _strategy_for_schema(schema_output.schema_type, schema_output.extraction_schema)
        results = strategy.extract(url=page.url, html_content=html)
    except Exception as exc:
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=page.url,
                label="apply schema",
                status="failed",
                duration=time.perf_counter() - apply_start_time,
                error=str(exc),
            ),
        )
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=page.url,
                label="extract",
                status="failed",
                duration=time.perf_counter() - total_start_time,
                error=str(exc),
            ),
        )
        warnings = run_quality_checks(url=page.url, html=html, extraction_results=[])
        return ExtractOutput(url=page.url, success=False, source=source, warnings=warnings, error=str(exc))

    warnings = run_quality_checks(
        url=page.url,
        html=html,
        crawl=page.crawl,
        extraction_results=results,
    )

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
            progress_callback=progress_callback,
        )
    )
