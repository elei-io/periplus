import asyncio
import json
import os
import time
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

from crawl4ai import JsonCssExtractionStrategy, LLMConfig
from dotenv import load_dotenv
from litellm import acompletion

from domains.cache import cache_domain, service_cache_root
from domains.crawl import CrawlMode, CrawlWait
from domains.progress import CrawlProgressCallback, CrawlProgressEvent, emit_crawl_progress
from domains.scrape.service import scrape as scrape_service

from .models import SchemaOutput, SchemaType

_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def _schema_llm_config() -> LLMConfig:
    load_dotenv(_ENV_PATH)
    provider = os.getenv(
        "OPENROUTER_SCHEMA_MODEL",
        os.getenv("OPENROUTER_SEARCH_EXTRACTOR_MODEL", "openai/gpt-4o"),
    )
    if not provider.startswith("openrouter/"):
        provider = f"openrouter/{provider}"

    return LLMConfig(
        provider=provider,
        api_token=os.getenv("OPENROUTER_API_KEY"),
    )


def _safe_cache_key(cache_key: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in cache_key)


def _schema_id(
    url: str,
    prompt: str,
    schema_type: SchemaType,
    cache_key: str | None,
    mode: CrawlMode,
    wait: CrawlWait,
) -> str:
    if cache_key:
        return _safe_cache_key(cache_key)

    parsed_url = urlparse(url)
    domain = _safe_cache_key(parsed_url.netloc or "schema")
    payload = {
        "domain": domain,
        "prompt": prompt,
        "schema_type": schema_type,
        "mode": mode,
        "wait": wait,
    }
    digest = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    return f"{domain}-{digest}"


def _schema_path(url: str, schema_id: str) -> Path:
    return service_cache_root(cache_domain(url), "schema") / f"{schema_id}.json"


def _html_path(output) -> str | None:
    if not output.pages:
        return None

    for artifact in output.pages[0].artifacts:
        if artifact.format == "html":
            return artifact.path

    return None


async def _scrape_html(
    url: str,
    mode: CrawlMode,
    wait: CrawlWait,
    progress_callback: CrawlProgressCallback | None,
) -> str:
    output = await scrape_service(
        urls=[url],
        mode=mode,
        wait=wait,
        progress_callback=progress_callback,
    )
    page = output.pages[0] if output.pages else None
    html_path = page.html_path if page else None
    if page is None or not page.success or html_path is None:
        error = page.error if page else "Scrape failed before producing a page."
        raise RuntimeError(error or "Scrape did not produce an HTML artifact.")

    return Path(html_path).read_text(encoding="utf-8")


def _read_cached_schema(path: Path, schema_type: SchemaType) -> tuple[dict, SchemaType]:
    cached = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(cached, dict) and isinstance(cached.get("schema"), dict):
        cached_schema_type = cached.get("schema_type", schema_type)
        return cached["schema"], cached_schema_type

    return cached, schema_type


def _write_cached_schema(path: Path, extraction_schema: dict, schema_type: SchemaType) -> None:
    payload = {
        "schema_type": schema_type,
        "schema": extraction_schema,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")


def _extract_json(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()

    parsed = json.loads(stripped)
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        parsed = parsed[0]

    if not isinstance(parsed, dict):
        raise ValueError("generated target JSON example must be an object")

    return json.dumps(parsed, separators=(",", ":"))


async def _generate_target_json_example(prompt: str) -> str:
    llm_config = _schema_llm_config()
    completion_kwargs = {"api_base": llm_config.base_url} if llm_config.base_url else {}
    response = await acompletion(
        model=llm_config.provider,
        api_key=llm_config.api_token,
        messages=[
            {
                "role": "system",
                "content": (
                    "Create a minimal target JSON example for a web extraction task. "
                    "Return only one valid JSON object. Do not return an array. "
                    "Do not include markdown, commentary, or placeholder ellipses."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Extraction prompt:\n"
                    f"{prompt}\n\n"
                    "Return one representative JSON object that shows the shape for a single extracted item."
                ),
            },
        ],
        temperature=0,
        max_tokens=500,
        **completion_kwargs,
    )
    content = response.choices[0].message.content or ""
    return _extract_json(content)


async def schema(
    url: str,
    prompt: str,
    target_json_example: str | None = None,
    schema_type: SchemaType = "css",
    cache_key: str | None = None,
    refresh: bool = False,
    html: str | None = None,
    mode: CrawlMode = "static",
    wait: CrawlWait = "none",
    progress_callback: CrawlProgressCallback | None = None,
) -> SchemaOutput:
    schema_id = _schema_id(
        url=url,
        prompt=prompt,
        schema_type=schema_type,
        cache_key=cache_key,
        mode=mode,
        wait=wait,
    )
    path = _schema_path(url, schema_id)
    if path.is_file() and not refresh:
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(url=url, label="schema cache", status="succeeded", duration=0.0),
        )
        extraction_schema, cached_schema_type = _read_cached_schema(path, schema_type)
        return SchemaOutput(
            schema_id=schema_id,
            schema_type=cached_schema_type,
            path=str(path),
            cached=True,
            extraction_schema=extraction_schema,
        )

    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(url=url, label="schema", status="started"),
    )
    start_time = time.perf_counter()
    target_json_example = target_json_example or await _generate_target_json_example(prompt)
    html = html or await _scrape_html(
        url=url,
        mode=mode,
        wait=wait,
        progress_callback=progress_callback,
    )
    try:
        generated_schema = await JsonCssExtractionStrategy.agenerate_schema(
            html=html,
            schema_type=schema_type.upper(),
            query=prompt,
            target_json_example=target_json_example,
            llm_config=_schema_llm_config(),
            validate=True,
        )
    except Exception as exc:
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=url,
                label="schema",
                status="failed",
                duration=time.perf_counter() - start_time,
                error=str(exc),
            ),
        )
        raise

    _write_cached_schema(path, generated_schema, schema_type)
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(
            url=url,
            label="schema",
            status="succeeded",
            duration=time.perf_counter() - start_time,
        ),
    )
    return SchemaOutput(
        schema_id=schema_id,
        schema_type=schema_type,
        path=str(path),
        cached=False,
        extraction_schema=generated_schema,
    )


def schema_sync(
    url: str,
    prompt: str,
    target_json_example: str | None = None,
    schema_type: SchemaType = "css",
    cache_key: str | None = None,
    refresh: bool = False,
    html: str | None = None,
    mode: CrawlMode = "static",
    wait: CrawlWait = "none",
    progress_callback: CrawlProgressCallback | None = None,
) -> SchemaOutput:
    return asyncio.run(
        schema(
            url=url,
            prompt=prompt,
            target_json_example=target_json_example,
            schema_type=schema_type,
            cache_key=cache_key,
            refresh=refresh,
            html=html,
            mode=mode,
            wait=wait,
            progress_callback=progress_callback,
        )
    )
