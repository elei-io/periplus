import asyncio
import json
import os
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

from crawl4ai import JsonCssExtractionStrategy, LLMConfig
from dotenv import load_dotenv
from litellm import acompletion

from .models import SchemaOutput, SchemaType

_SCHEMA_ROOT = Path(__file__).resolve().parents[2] / ".schemas"
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
) -> str:
    if cache_key:
        return _safe_cache_key(cache_key)

    parsed_url = urlparse(url)
    domain = _safe_cache_key(parsed_url.netloc or "schema")
    payload = {"domain": domain, "prompt": prompt, "schema_type": schema_type}
    digest = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    return f"{domain}-{digest}"


def _schema_path(schema_id: str) -> Path:
    return _SCHEMA_ROOT / f"{schema_id}.json"


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
) -> SchemaOutput:
    schema_id = _schema_id(
        url=url,
        prompt=prompt,
        schema_type=schema_type,
        cache_key=cache_key,
    )
    path = _schema_path(schema_id)
    if path.is_file() and not refresh:
        return SchemaOutput(
            schema_id=schema_id,
            schema_type=schema_type,
            path=str(path),
            cached=True,
            extraction_schema=json.loads(path.read_text(encoding="utf-8")),
        )

    target_json_example = target_json_example or await _generate_target_json_example(prompt)
    generated_schema = await JsonCssExtractionStrategy.agenerate_schema(
        url=url,
        schema_type=schema_type,
        query=prompt,
        target_json_example=target_json_example,
        llm_config=_schema_llm_config(),
        validate=True,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(generated_schema, indent=2)}\n", encoding="utf-8")
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
) -> SchemaOutput:
    return asyncio.run(
        schema(
            url=url,
            prompt=prompt,
            target_json_example=target_json_example,
            schema_type=schema_type,
            cache_key=cache_key,
            refresh=refresh,
        )
    )
