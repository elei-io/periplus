import asyncio
import json
import time
from hashlib import sha256
from urllib.parse import urlparse
from uuid import UUID

from crawl4ai import JsonCssExtractionStrategy, LLMConfig
from litellm import acompletion
from sqlalchemy.orm import Session

from actions.crawl.service import crawl as crawl_service
from actions.shared.llm import openrouter_llm_config
from actions.shared.cache import CacheOptions
from actions.shared.progress import ProgressReporter, ProgressEvent, emit_progress
from control.data_schemas.service import (
    create_data_schema,
    default_match_for_url,
    find_reusable_data_schema,
    record_data_schema_use,
    replace_data_schema,
)
from runtime.context import commit_task_checkpoint

from .schemas import SchemaOutput, SchemaType

def _schema_llm_config() -> LLMConfig:
    return openrouter_llm_config("OPENROUTER_SCHEMA_MODEL", "OPENROUTER_SEARCH_EXTRACTOR_MODEL")


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value)


def _schema_id(
    url: str,
    prompt: str,
    schema_type: SchemaType,
    schema_id: str | None,
) -> str:
    if schema_id:
        return _safe_id(schema_id)

    parsed_url = urlparse(url)
    domain = _safe_id(parsed_url.netloc or "schema")
    payload = {
        "domain": domain,
        "prompt": prompt,
        "schema_type": schema_type,
    }
    digest = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    return f"{domain}-{digest}"


async def _crawl_html(
    url: str,
    progress_reporter: ProgressReporter | None,
    session: Session | None,
    task_run_id: UUID | None,
    cache: CacheOptions | dict[str, object] | None,
) -> tuple[str, UUID | None, str | None]:
    output = await crawl_service(
        urls=[url],
        progress_reporter=progress_reporter,
        session=session,
        task_run_id=task_run_id,
        cache=cache,
        include_links=False,
    )
    page = output.pages[0] if output.pages else None
    if page is None or not page.success or page.html is None:
        error = page.error if page else "Crawl failed before producing a page."
        raise RuntimeError(error or "Crawl did not produce HTML.")

    return page.html, page.crawl_id, page.document_id


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
    schema_id: str | None = None,
    html: str | None = None,
    crawl_id: UUID | None = None,
    document_id: str | None = None,
    progress_reporter: ProgressReporter | None = None,
    session: Session | None = None,
    task_run_id: UUID | None = None,
    match: str | None = None,
    reuse_existing: bool = True,
    replace_schema_id: UUID | None = None,
    cache: CacheOptions | dict[str, object] | None = None,
) -> SchemaOutput:
    schema_id = _schema_id(
        url=url,
        prompt=prompt,
        schema_type=schema_type,
        schema_id=schema_id,
    )
    await emit_progress(
        progress_reporter,
        ProgressEvent(resource=url, phase="schema", status="started", message="Preparing an extraction schema."),
    )
    start_time = time.perf_counter()
    match_value = match or default_match_for_url(url)
    if session is not None and reuse_existing and replace_schema_id is None:
        existing = find_reusable_data_schema(
            session,
            url=url,
            prompt=prompt,
            schema_type=schema_type,
            target_json_example=target_json_example,
        )
        if existing is not None:
            record_data_schema_use(session, task_run_id=task_run_id, schema=existing)
            commit_task_checkpoint(session)
            await emit_progress(
                progress_reporter,
                ProgressEvent(
                    resource=url,
                    phase="schema",
                    status="succeeded",
                    message="Reused an existing extraction schema.",
                    metadata={"reused": True, "schema_type": schema_type},
                    duration=time.perf_counter() - start_time,
                ),
            )
            return SchemaOutput(
                schema_id=str(existing.id),
                schema_type=schema_type,
                extraction_schema=existing.schema_json,
            )
        commit_task_checkpoint(session)

    target_json_example = target_json_example or await _generate_target_json_example(prompt)
    if html is None:
        html, crawl_id, document_id = await _crawl_html(
            url=url,
            progress_reporter=progress_reporter,
            session=session,
            task_run_id=task_run_id,
            cache=cache,
        )
    await emit_progress(
        progress_reporter,
        ProgressEvent(
            resource=url,
            phase="generate_schema",
            status="started",
            message="Generating and validating the extraction schema.",
            metadata={"schema_type": schema_type},
        ),
    )
    generation_start_time = time.perf_counter()
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
        await emit_progress(
            progress_reporter,
            ProgressEvent(
                resource=url,
                phase="generate_schema",
                status="failed",
                message="Schema generation failed.",
                duration=time.perf_counter() - generation_start_time,
                error=str(exc),
            ),
        )
        raise

    await emit_progress(
        progress_reporter,
        ProgressEvent(
            resource=url,
            phase="generate_schema",
            status="succeeded",
            message="Extraction schema generated and validated.",
            metadata={"schema_type": schema_type},
            duration=time.perf_counter() - generation_start_time,
        ),
    )
    await emit_progress(
        progress_reporter,
        ProgressEvent(
            resource=url,
            phase="schema",
            status="succeeded",
            message="Extraction schema ready.",
            metadata={"reused": False, "schema_type": schema_type},
            duration=time.perf_counter() - start_time,
        ),
    )
    if session is not None:
        inputs_json = {
            "url": url,
            "schema_id": schema_id,
            "match": match_value,
        }
        if replace_schema_id is not None:
            durable_schema = replace_data_schema(
                session,
                schema_id=replace_schema_id,
                prompt=prompt,
                schema_type=schema_type,
                target_json_example=target_json_example,
                match=match,
                schema_json=generated_schema,
                task_run_id=task_run_id,
                crawl_id=crawl_id,
                document_id=document_id,
                inputs_json=inputs_json,
            )
        else:
            durable_schema = create_data_schema(
                session,
                url=url,
                prompt=prompt,
                schema_type=schema_type,
                target_json_example=target_json_example,
                match=match_value,
                schema_json=generated_schema,
                task_run_id=task_run_id,
                crawl_id=crawl_id,
                document_id=document_id,
                inputs_json=inputs_json,
            )
        record_data_schema_use(session, task_run_id=task_run_id, schema=durable_schema)
        commit_task_checkpoint(session)
        return SchemaOutput(
            schema_id=str(durable_schema.id),
            schema_type=schema_type,
            extraction_schema=generated_schema,
        )

    return SchemaOutput(
        schema_id=schema_id,
        schema_type=schema_type,
        extraction_schema=generated_schema,
    )


def schema_sync(
    url: str,
    prompt: str,
    target_json_example: str | None = None,
    schema_type: SchemaType = "css",
    schema_id: str | None = None,
    html: str | None = None,
    progress_reporter: ProgressReporter | None = None,
    cache: CacheOptions | dict[str, object] | None = None,
) -> SchemaOutput:
    return asyncio.run(
        schema(
            url=url,
            prompt=prompt,
            target_json_example=target_json_example,
            schema_type=schema_type,
            schema_id=schema_id,
            html=html,
            progress_reporter=progress_reporter,
            cache=cache,
        )
    )
