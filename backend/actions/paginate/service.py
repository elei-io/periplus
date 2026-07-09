import asyncio
import json
import os
import time
from hashlib import sha1
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from uuid import UUID

from crawl4ai import AsyncWebCrawler, LLMConfig
from dotenv import load_dotenv
from litellm import acompletion
from lxml import html as lxml_html
from sqlalchemy.orm import Session

from actions.crawl.service import crawl_one_for_task
from actions.shared.crawl import CrawlMode, CrawlWait, browser_config_for_mode
from actions.shared.llm import openrouter_llm_config
from actions.shared.progress import CrawlProgressCallback, CrawlProgressEvent, emit_crawl_progress
from pagination_schemas.models import PaginationSchema
from pagination_schemas.service import (
    create_pagination_schema,
    default_match_for_url,
    find_reusable_pagination_schema,
    mark_pagination_schema_failed,
)

from .schemas import PaginatedPage, PaginateOutput, PaginationPlan

_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
_DEFAULT_PAGINATION_ATTEMPTS = 3


def _llm_config() -> LLMConfig:
    return openrouter_llm_config(
        "OPENROUTER_PAGINATION_MODEL",
        "OPENROUTER_SCHEMA_MODEL",
        "OPENROUTER_SEARCH_EXTRACTOR_MODEL",
    )


def _extract_json(value: str) -> dict[str, Any]:
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        preview = stripped[:500].replace("\n", " ")
        raise ValueError(f"pagination agent returned invalid JSON: {exc.msg} at char {exc.pos}; preview={preview!r}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("pagination agent returned a non-object response")
    return parsed


def _html_excerpt(html: str, max_chars: int = 24000) -> str:
    # Keep beginning and end because pagination controls often live near either edge.
    if len(html) <= max_chars:
        return html
    half = max_chars // 2
    return f"{html[:half]}\n<!-- ATLAS_TRUNCATED -->\n{html[-half:]}"


def _selector_items(html: str | None, selector: str) -> list[str]:
    if not html or not selector:
        return []
    try:
        document = lxml_html.fromstring(html)
        nodes = document.cssselect(selector)
    except Exception:
        return []
    values: list[str] = []
    for node in nodes:
        text = " ".join(node.text_content().split())
        href = node.get("href") or ""
        src = node.get("src") or ""
        values.append((href or src or text)[:500])
    return values


def _next_button_status(html: str | None, selector: str | None) -> str:
    if not html or not selector:
        return "unknown"
    try:
        document = lxml_html.fromstring(html)
        nodes = document.cssselect(selector)
    except Exception:
        return "unknown"
    if not nodes:
        return "missing"

    for node in nodes:
        attrs = {key.lower(): value for key, value in node.attrib.items()}
        classes = set((attrs.get("class") or "").lower().replace("_", "-").split())
        aria_disabled = (attrs.get("aria-disabled") or "").lower() == "true"
        disabled = "disabled" in attrs or aria_disabled or "disabled" in classes or "is-disabled" in classes
        href = attrs.get("href")
        inert_link = node.tag.lower() == "a" and (href is None or href.strip() in {"", "#"})
        if not disabled and not inert_link:
            return "enabled"
    return "disabled"


def _warnings() -> list[str]:
    return []


def _max_pagination_attempts() -> int:
    load_dotenv(_ENV_PATH)
    try:
        return max(1, int(os.getenv("MAX_PAGINATION_ATTEMPTS", str(_DEFAULT_PAGINATION_ATTEMPTS))))
    except ValueError:
        return _DEFAULT_PAGINATION_ATTEMPTS


def _query_url(current_url: str, plan: PaginationPlan, page_index: int) -> str | None:
    if not plan.query_param_key:
        return None
    value = plan.start_value + (page_index * plan.value_step)
    raw_value = (
        plan.query_param_value_template.replace("{{value}}", str(value))
        .replace("{value}", str(value))
        .replace("%{value}", str(value))
    )

    parsed = urlparse(current_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params[plan.query_param_key] = [raw_value]
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


async def _generate_pagination_candidate(
    *,
    url: str,
    html: str,
    previous_errors: list[str],
    progress_callback: CrawlProgressCallback | None,
) -> dict[str, Any]:
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(url=url, label="generate pagination candidate", status="started"),
    )
    started = time.perf_counter()
    config = _llm_config()
    completion_kwargs = {"api_base": config.base_url} if config.base_url else {}
    response = await acompletion(
        model=config.provider,
        api_key=config.api_token,
        messages=[
            {
                "role": "system",
                "content": (
                    "You create query-parameter pagination schemas for web pages. Return only JSON. "
                    "Find the repeated item selector and the URL query parameter used to request the next result page. "
                    "Do not use click or scroll strategies. query_param_value_template must be generic, using {{value}} "
                    "for the numeric part, such as {{value}}, v1:{{value}}, or offset-{{value}}. Store raw values, not URL-encoded values. "
                    "Return a single compact JSON object with no markdown and no unescaped newlines inside string values."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Start URL: {url}\n\n"
                    "Return this JSON object:\n"
                    "{\n"
                    '  "next_button_selector": "CSS selector for next button/link if present, else null",\n'
                    '  "item_selector": "CSS selector for repeated items",\n'
                    '  "expected_max_item_count": 30,\n'
                    '  "query_param_key": "page",\n'
                    '  "query_param_value_template": "{{value}}",\n'
                    '  "start_value": 1,\n'
                    '  "value_step": 1,\n'
                    '  "match": "URL match pattern, usually query-stripped path plus *",\n'
                    '  "confidence": 0.0,\n'
                    '  "evidence": ["short evidence strings"]\n'
                    "}\n\n"
                    "start_value is the value represented by the current page. If page 1 has no param and page 2 should be page=2, use start_value=1. "
                    "If page 1 has no param and page 2 should be page_token=v1:1, use start_value=0 and query_param_value_template=v1:{{value}}. "
                    "Use value_step for offset-style pagination, e.g. start_value=0, value_step=30 gives page 2 value 30.\n\n"
                    f"Previous failed candidate errors:\n{json.dumps(previous_errors)}\n\n"
                    f"HTML:\n{_html_excerpt(html)}"
                ),
            },
        ],
        temperature=0,
        max_tokens=1200,
        **completion_kwargs,
    )
    try:
        data = _extract_json(response.choices[0].message.content or "")
    except Exception as exc:
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=url,
                label="generate pagination candidate",
                status="failed",
                duration=time.perf_counter() - started,
                error=str(exc),
            ),
        )
        raise
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(
            url=url,
            label="generate pagination candidate",
            status="succeeded",
            duration=time.perf_counter() - started,
        ),
    )
    return data


async def _validate_items(
    *,
    url: str,
    html: str | None,
    item_selector: str,
    page_label: str,
    progress_callback: CrawlProgressCallback | None,
) -> list[str]:
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(url=url, label=f"verify {page_label} items", status="started"),
    )
    started = time.perf_counter()
    items = _selector_items(html, item_selector)
    if not items:
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=url,
                label=f"verify {page_label} items",
                status="failed",
                duration=time.perf_counter() - started,
                error=f"selector {item_selector!r} found 0 items",
            ),
        )
        return []
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(
            url=url,
            label=f"verify {page_label} items ({len(items)})",
            status="succeeded",
            duration=time.perf_counter() - started,
        ),
    )
    return items


def _plan_from_schema(schema: PaginationSchema, *, reused: bool) -> PaginationPlan:
    meta = schema.inputs_json.get("agent") if isinstance(schema.inputs_json, dict) else {}
    return PaginationPlan(
        schema_id=schema.id,
        kind="query",
        next_button_selector=schema.next_button_selector,
        item_selector=schema.item_selector,
        expected_max_item_count=schema.expected_max_item_count,
        query_param_key=schema.query_param_key,
        query_param_value_template=schema.query_param_value_template,
        start_value=schema.start_value,
        value_step=schema.value_step,
        match=schema.match,
        reused=reused,
        confidence=(meta or {}).get("confidence"),
        evidence=(meta or {}).get("evidence") or [],
    )


def _session_id(url: str) -> str:
    return f"atlas-paginate-{sha1(url.encode()).hexdigest()}"


def _page_result(
    *,
    page: Any,
    index: int,
    items: list[str],
    new_items: list[str],
    success: bool | None = None,
    error: str | None = None,
) -> PaginatedPage:
    return PaginatedPage(
        index=index,
        url=page.url,
        crawl_id=page.crawl_id,
        artifact_ids=page.artifact_ids,
        item_count=len(items),
        new_item_count=len(new_items),
        success=page.success if success is None else success,
        error=page.error or error,
    )


async def _advance_page(
    *,
    crawler: AsyncWebCrawler,
    plan: PaginationPlan,
    current_url: str,
    start_url: str,
    page_index: int,
    mode: CrawlMode,
    wait: CrawlWait,
    session: Session,
    task_run_id: UUID,
    progress_callback: CrawlProgressCallback | None,
) -> Any:
    next_url = _query_url(start_url, plan, page_index)
    if next_url is None:
        return None
    return await crawl_one_for_task(
        url=next_url,
        mode=mode,
        wait=wait,
        index=page_index,
        progress_callback=progress_callback,
        session=session,
        task_run_id=task_run_id,
        crawler=crawler,
    )


async def paginate(
    url: str,
    max_pages: int = 5,
    mode: CrawlMode = "app",
    wait: CrawlWait = "stable",
    reuse_existing: bool = True,
    progress_callback: CrawlProgressCallback | None = None,
    session: Session | None = None,
    task_run_id: UUID | None = None,
) -> PaginateOutput:
    if session is None or task_run_id is None:
        raise RuntimeError("paginate primitive requires task-run execution context")

    async with AsyncWebCrawler(config=browser_config_for_mode(mode)) as crawler:
        first_page = await crawl_one_for_task(
            url=url,
            mode=mode,
            wait=wait,
            index=0,
            progress_callback=progress_callback,
            session=session,
            task_run_id=task_run_id,
            run_config_overrides={"session_id": _session_id(url)},
            crawler=crawler,
        )
        output = await _paginate_from_first_page(
            crawler=crawler,
            url=url,
            max_pages=max_pages,
            mode=mode,
            wait=wait,
            reuse_existing=reuse_existing,
            progress_callback=progress_callback,
            session=session,
            task_run_id=task_run_id,
            first_page=first_page,
        )

    return output


async def _paginate_from_first_page(
    *,
    crawler: AsyncWebCrawler,
    url: str,
    max_pages: int,
    mode: CrawlMode,
    wait: CrawlWait,
    reuse_existing: bool,
    progress_callback: CrawlProgressCallback | None,
    session: Session,
    task_run_id: UUID,
    first_page: Any,
) -> PaginateOutput:
    if not first_page.success or not first_page.html:
        return PaginateOutput(
            url=url,
            pages=[
                PaginatedPage(
                    index=0,
                    url=first_page.url,
                    crawl_id=first_page.crawl_id,
                    artifact_ids=first_page.artifact_ids,
                    item_count=0,
                    new_item_count=0,
                    success=False,
                    error=first_page.error,
                )
            ],
            stopped_reason="first_page_failed",
            warnings=_warnings(),
        )

    schema = find_reusable_pagination_schema(session, url=first_page.url) if reuse_existing else None
    plan: PaginationPlan | None = None
    pages: list[PaginatedPage] = []
    seen_items: set[str] = set()
    current_url = first_page.url
    last_page_html = first_page.html
    start_page_index = 1

    if schema is not None:
        plan = _plan_from_schema(schema, reused=True)
        first_items = await _validate_items(
            url=first_page.url,
            html=first_page.html,
            item_selector=plan.item_selector,
            page_label="page 1",
            progress_callback=progress_callback,
        )
        if first_items:
            seen_items = set(first_items)
            pages.append(_page_result(page=first_page, index=0, items=first_items, new_items=first_items))
        else:
            mark_pagination_schema_failed(session, schema, "reused pagination schema matched 0 items on first page")
            schema = None
            plan = None

    candidate_errors: list[str] = []
    if plan is None:
        attempts = _max_pagination_attempts()
        for attempt in range(1, attempts + 1):
            await emit_crawl_progress(
                progress_callback,
                CrawlProgressEvent(
                    url=first_page.url,
                    label=f"pagination candidate {attempt}/{attempts}",
                    status="started",
                ),
            )
            try:
                candidate = await _generate_pagination_candidate(
                    url=first_page.url,
                    html=first_page.html,
                    previous_errors=candidate_errors,
                    progress_callback=progress_callback,
                )
            except Exception as exc:
                error = str(exc)
                candidate_errors.append(error)
                await emit_crawl_progress(
                    progress_callback,
                    CrawlProgressEvent(
                        url=first_page.url,
                        label=f"pagination candidate {attempt}/{attempts}",
                        status="failed",
                        error=error,
                    ),
                )
                continue
            item_selector = str(candidate.get("item_selector") or "").strip()
            first_items = await _validate_items(
                url=first_page.url,
                html=first_page.html,
                item_selector=item_selector,
                page_label="page 1",
                progress_callback=progress_callback,
            )
            if not first_items:
                candidate_errors.append(f"{item_selector!r} found 0 items on page 1")
                await emit_crawl_progress(
                    progress_callback,
                    CrawlProgressEvent(
                        url=first_page.url,
                        label=f"pagination candidate {attempt}/{attempts}",
                        status="failed",
                        error=candidate_errors[-1],
                    ),
                )
                continue

            candidate_plan = PaginationPlan(
                schema_id=None,
                kind="query",
                next_button_selector=candidate.get("next_button_selector"),
                item_selector=item_selector,
                expected_max_item_count=candidate.get("expected_max_item_count") or len(first_items),
                query_param_key=str(candidate.get("query_param_key") or "").strip(),
                query_param_value_template=str(candidate.get("query_param_value_template") or "{{value}}").strip(),
                start_value=int(candidate.get("start_value", 0)),
                value_step=max(1, int(candidate.get("value_step", 1))),
                match=candidate.get("match") or default_match_for_url(first_page.url),
                reused=False,
                confidence=candidate.get("confidence"),
                evidence=candidate.get("evidence") or [],
            )
            if not candidate_plan.query_param_key:
                error = "candidate did not provide query_param_key"
                candidate_errors.append(error)
                await emit_crawl_progress(
                    progress_callback,
                    CrawlProgressEvent(
                        url=first_page.url,
                        label=f"pagination candidate {attempt}/{attempts}",
                        status="failed",
                        error=error,
                    ),
                )
                continue
            next_status = _next_button_status(first_page.html, candidate_plan.next_button_selector)
            if next_status in {"missing", "disabled"}:
                error = f"next button is {next_status} on page 1"
                candidate_errors.append(error)
                await emit_crawl_progress(
                    progress_callback,
                    CrawlProgressEvent(
                        url=first_page.url,
                        label=f"pagination candidate {attempt}/{attempts}",
                        status="failed",
                        error=error,
                    ),
                )
                continue
            next_page = await _advance_page(
                crawler=crawler,
                plan=candidate_plan,
                current_url=first_page.url,
                start_url=url,
                page_index=1,
                mode=mode,
                wait=wait,
                session=session,
                task_run_id=task_run_id,
                progress_callback=progress_callback,
            )
            if next_page is None or not next_page.success:
                error = "candidate did not produce a successful second page"
                if next_page is not None and next_page.error:
                    error = next_page.error
                candidate_errors.append(error)
                await emit_crawl_progress(
                    progress_callback,
                    CrawlProgressEvent(
                        url=first_page.url,
                        label=f"pagination candidate {attempt}/{attempts}",
                        status="failed",
                        error=error,
                    ),
                )
                continue

            second_items = await _validate_items(
                url=next_page.url,
                html=next_page.html,
                item_selector=item_selector,
                page_label="page 2",
                progress_callback=progress_callback,
            )
            new_items = [item for item in second_items if item not in set(first_items)]
            if not second_items or not new_items:
                error = (
                    f"candidate produced {len(second_items)} page 2 items and {len(new_items)} new items"
                )
                candidate_errors.append(error)
                await emit_crawl_progress(
                    progress_callback,
                    CrawlProgressEvent(
                        url=next_page.url,
                        label=f"pagination candidate {attempt}/{attempts}",
                        status="failed",
                        error=error,
                    ),
                )
                continue

            schema = create_pagination_schema(
                session,
                url=first_page.url,
                match=candidate_plan.match,
                next_button_selector=candidate_plan.next_button_selector,
                item_selector=item_selector,
                expected_max_item_count=candidate_plan.expected_max_item_count,
                query_param_key=candidate_plan.query_param_key,
                query_param_value_template=candidate_plan.query_param_value_template,
                start_value=candidate_plan.start_value,
                value_step=candidate_plan.value_step,
                task_run_id=task_run_id,
                generated_from_crawl_id=first_page.crawl_id,
                generated_from_artifact_id=first_page.artifact_ids[0] if first_page.artifact_ids else None,
                inputs_json={
                    "url": first_page.url,
                    "mode": mode,
                    "wait": wait,
                    "agent": {
                        "confidence": candidate_plan.confidence,
                        "evidence": candidate_plan.evidence,
                        "candidate": candidate,
                        "validation": {
                            "page_1_item_count": len(first_items),
                            "page_2_item_count": len(second_items),
                            "page_2_new_item_count": len(new_items),
                            "attempt": attempt,
                        },
                    },
                },
            )
            plan = _plan_from_schema(schema, reused=False)
            seen_items = set(first_items)
            pages = [
                _page_result(page=first_page, index=0, items=first_items, new_items=first_items),
                _page_result(page=next_page, index=1, items=second_items, new_items=new_items),
            ]
            seen_items.update(new_items)
            current_url = next_page.url
            last_page_html = next_page.html
            start_page_index = 2
            await emit_crawl_progress(
                progress_callback,
                CrawlProgressEvent(
                    url=next_page.url,
                    label=f"pagination candidate {attempt}/{attempts}",
                    status="succeeded",
                ),
            )
            break

    if plan is None:
        return PaginateOutput(
            url=url,
            pages=[
                _page_result(page=first_page, index=0, items=[], new_items=[], success=True),
            ],
            plan=None,
            stopped_reason="schema_validation_failed",
            warnings=candidate_errors or _warnings(),
        )

    stopped_reason = "max_pages"
    for page_index in range(start_page_index, max_pages):
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(url=current_url, label=f"paginate page {page_index + 1}", status="started"),
        )
        started = time.perf_counter()
        next_status = _next_button_status(last_page_html, plan.next_button_selector)
        if next_status in {"missing", "disabled"}:
            stopped_reason = f"next_button_{next_status}"
            break
        next_page = await _advance_page(
            crawler=crawler,
            plan=plan,
            current_url=current_url,
            start_url=url,
            page_index=page_index,
            mode=mode,
            wait=wait,
            session=session,
            task_run_id=task_run_id,
            progress_callback=progress_callback,
        )
        if next_page is None:
            stopped_reason = "no_next"
            break

        items = await _validate_items(
            url=next_page.url,
            html=next_page.html,
            item_selector=plan.item_selector,
            page_label=f"page {page_index + 1}",
            progress_callback=progress_callback,
        )
        new_items = [item for item in items if item not in seen_items]
        pages.append(_page_result(page=next_page, index=page_index, items=items, new_items=new_items))
        if not next_page.success:
            stopped_reason = "page_failed"
            break
        if not new_items:
            stopped_reason = "no_new_items"
            break

        seen_items.update(new_items)
        current_url = next_page.url
        last_page_html = next_page.html
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=current_url,
                label=f"paginate page {page_index + 1}",
                status="succeeded",
                duration=time.perf_counter() - started,
            ),
        )

    return PaginateOutput(
        url=url,
        pages=pages,
        plan=plan,
        stopped_reason=stopped_reason,
        warnings=_warnings(),
    )


def paginate_sync(
    url: str,
    max_pages: int = 5,
    mode: CrawlMode = "app",
    wait: CrawlWait = "stable",
    reuse_existing: bool = True,
) -> PaginateOutput:
    return asyncio.run(
        paginate(
            url=url,
            max_pages=max_pages,
            mode=mode,
            wait=wait,
            reuse_existing=reuse_existing,
        )
    )
