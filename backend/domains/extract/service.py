import asyncio
import time
from pathlib import Path

from crawl4ai import JsonCssExtractionStrategy, JsonXPathExtractionStrategy

from domains.crawl import CrawlMode, CrawlWait
from domains.progress import CrawlProgressCallback, CrawlProgressEvent, emit_crawl_progress
from domains.schema.models import SchemaType
from domains.schema.service import schema as schema_service
from domains.scrape.service import scrape as scrape_service

from .models import ExtractOutput, ExtractSource


def _html_path(output) -> str | None:
    if not output.pages:
        return None

    return output.pages[0].html_path


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
    refresh_schema: bool = False,
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

    html_path = _html_path(scrape_output)
    if html_path is None:
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=page.url,
                label="extract",
                status="failed",
                duration=time.perf_counter() - total_start_time,
                error="Scrape did not produce an HTML artifact.",
            ),
        )
        return ExtractOutput(url=page.url, success=False, error="Scrape did not produce an HTML artifact.")

    try:
        html = Path(html_path).read_text(encoding="utf-8")
    except Exception as exc:
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
        return ExtractOutput(url=page.url, success=False, error=str(exc))

    schema_output = await schema_service(
        url=page.url,
        prompt=prompt,
        target_json_example=target_json_example,
        schema_type=schema_type,
        refresh=refresh_schema,
        html=html,
        mode=mode,
        wait=wait,
        progress_callback=progress_callback,
    )
    source = ExtractSource(
        scrape_cache_dir=page.cache_dir,
        html_path=html_path,
        scrape_cached=page.cached,
        schema_id=schema_output.schema_id,
        schema_path=schema_output.path,
        schema_cached=schema_output.cached,
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
        return ExtractOutput(url=page.url, success=False, source=source, error=str(exc))

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
    return ExtractOutput(url=page.url, success=True, source=source, results=results)


def extract_sync(
    url: str,
    prompt: str,
    target_json_example: str | None = None,
    schema_type: SchemaType = "css",
    mode: CrawlMode = "static",
    wait: CrawlWait = "none",
    refresh_schema: bool = False,
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
            refresh_schema=refresh_schema,
            progress_callback=progress_callback,
        )
    )
