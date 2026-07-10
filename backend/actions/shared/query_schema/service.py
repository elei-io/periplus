from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from uuid import UUID

from crawl4ai import LLMConfig
from litellm import acompletion
from lxml import html as lxml_html
from sqlalchemy.orm import Session

from actions.shared.llm import openrouter_llm_config
from actions.shared.progress import ProgressReporter, ProgressEvent, emit_progress
from query_schemas.service import find_query_schema_for_url, upsert_query_schema
from tasks.context import commit_task_checkpoint

from .schemas import QueryParamCandidate, QueryParamGroup, QueryParamOutput, QueryParamResult, QuerySchema

_URL_ATTRS = {
    "href",
    "action",
    "formaction",
    "src",
    "data-url",
    "data-href",
    "data-action",
    "data-target-url",
    "data-next-url",
    "data-page-url",
}


def _llm_config() -> LLMConfig:
    return openrouter_llm_config(
        "OPENROUTER_QUERY_PARAM_MODEL",
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
        raise ValueError(f"query param agent returned invalid JSON: {exc.msg} at char {exc.pos}; preview={preview!r}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("query param agent returned a non-object response")
    return parsed


def _page_path(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    normalized_parts: list[str] = []
    for part in parts:
        if normalized_parts and normalized_parts[-1] == part:
            continue
        normalized_parts.append(part)
    return "/" + "/".join(normalized_parts) if normalized_parts else "/"


def _page_key(url: str) -> tuple[str, str, str, str]:
    parsed = urlparse(url)
    return (parsed.scheme.lower(), parsed.netloc.lower(), _page_path(parsed.path or "/"), parsed.params)


def _same_page(candidate_url: str, page_url: str) -> bool:
    return _page_key(candidate_url) == _page_key(page_url)


def _query_stripped(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.params, "", ""))


def _short_text(value: str | None, max_length: int = 120) -> str | None:
    if not value:
        return None
    normalized = " ".join(value.split())
    return normalized[:max_length] if normalized else None


def _css_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _css_selector(element: Any) -> str:
    parts: list[str] = []
    current = element
    while current is not None and isinstance(getattr(current, "tag", None), str):
        tag = str(current.tag).lower()
        element_id = current.attrib.get("id")
        if element_id:
            parts.append(f'{tag}[id="{_css_escape(element_id)}"]')
            break

        classes = [class_name for class_name in current.attrib.get("class", "").split() if class_name]
        part = tag + "".join(f".{class_name}" for class_name in classes[:2])
        parent = current.getparent()
        if parent is not None:
            siblings = [
                sibling
                for sibling in parent
                if isinstance(getattr(sibling, "tag", None), str) and str(sibling.tag).lower() == tag
            ]
            if len(siblings) > 1:
                part = f"{part}:nth-of-type({siblings.index(current) + 1})"
        parts.append(part)
        current = parent

    return " > ".join(reversed(parts[-5:]))


def _candidate_from_url(
    *,
    page_url: str,
    candidate_url: str,
    source: str,
    selector: str | None = None,
    text: str | None = None,
    attributes: dict[str, str] | None = None,
) -> QueryParamCandidate | None:
    absolute_url = urljoin(page_url, candidate_url)
    parsed = urlparse(absolute_url)
    if not parsed.query or not _same_page(absolute_url, page_url):
        return None
    return QueryParamCandidate(
        url=absolute_url,
        source=source,
        selector=selector,
        text=_short_text(text),
        attributes=attributes or {},
    )


def _form_candidate_url(page_url: str, form: Any, fields: list[tuple[str, str]]) -> str | None:
    if not fields:
        return None

    action = form.attrib.get("action") or page_url
    absolute_url = urljoin(page_url, action)
    parsed = urlparse(absolute_url)
    existing_fields = parse_qsl(parsed.query, keep_blank_values=True)
    query = urlencode([*existing_fields, *fields], doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.params, query, ""))


def _form_controls(form: Any) -> tuple[list[tuple[str, str]], list[tuple[Any, str | None, str | None]]]:
    fields: list[tuple[str, str]] = []
    submits: list[tuple[Any, str | None, str | None]] = []
    for control in form.iterdescendants():
        if not isinstance(getattr(control, "tag", None), str):
            continue
        tag = str(control.tag).lower()
        name = control.attrib.get("name")

        if tag == "input":
            input_type = control.attrib.get("type", "text").lower()
            value = control.attrib.get("value", "")
            if input_type in {"submit", "button", "image"}:
                submits.append((control, name, value or _short_text(control.text_content()) or None))
                continue
            if input_type in {"reset", "file"}:
                continue
            if name:
                fields.append((name, value))
            continue

        if tag == "button":
            button_type = control.attrib.get("type", "submit").lower()
            if button_type == "submit":
                submits.append((control, name, control.attrib.get("value") or _short_text(control.text_content())))
            continue

        if tag == "select" and name:
            selected_options = [
                option
                for option in control.iterdescendants("option")
                if "selected" in option.attrib
            ]
            options = selected_options or list(control.iterdescendants("option"))[:1]
            for option in options:
                fields.append((name, option.attrib.get("value", _short_text(option.text_content()) or "")))
            continue

        if tag == "textarea" and name:
            fields.append((name, control.text or ""))

    return fields, submits


def _collect_form_candidates(page_url: str, document: Any) -> list[QueryParamCandidate]:
    candidates: list[QueryParamCandidate] = []
    for form in document.iter("form"):
        if not isinstance(getattr(form, "tag", None), str):
            continue
        method = form.attrib.get("method", "get").lower()

        fields, submits = _form_controls(form)
        submit_controls = submits or [(form, None, _short_text(form.text_content()))]
        for submit, submit_name, submit_value in submit_controls:
            submit_fields = list(fields)
            if submit_name:
                submit_fields.append((submit_name, submit_value or ""))
            candidate_url = _form_candidate_url(page_url, form, submit_fields)
            if candidate_url is None:
                continue
            candidate = _candidate_from_url(
                page_url=page_url,
                candidate_url=candidate_url,
                source=f"form[{method or 'get'}]",
                selector=_css_selector(form),
                text=submit_value or _short_text(form.text_content()),
                attributes={
                    "action": form.attrib.get("action", ""),
                    "method": method or "get",
                },
            )
            if candidate is not None:
                candidates.append(candidate)

    return candidates


def _collect_candidates(page_url: str, html: str) -> list[QueryParamCandidate]:
    candidates: list[QueryParamCandidate] = []
    seen: set[tuple[str, str]] = set()

    try:
        document = lxml_html.fromstring(html)
    except Exception:
        return candidates

    for candidate in _collect_form_candidates(page_url, document):
        key = (candidate.url, candidate.source)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)

    for element in document.iter():
        if not isinstance(element.tag, str):
            continue
        attrs = {str(key): str(value) for key, value in element.attrib.items()}
        text = _short_text(element.text_content())
        source = str(element.tag)
        for attr_name, attr_value in attrs.items():
            if attr_name not in _URL_ATTRS and not (attr_name.startswith("data-") and "url" in attr_name):
                continue
            candidate = _candidate_from_url(
                page_url=page_url,
                candidate_url=attr_value,
                source=f"{source}[{attr_name}]",
                selector=_css_selector(element),
                text=text,
                attributes={attr_name: attr_value},
            )
            if candidate is None:
                continue
            key = (candidate.url, candidate.source)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)

    return candidates[:200]


def _fallback_params(candidates: list[QueryParamCandidate]) -> list[QueryParamGroup]:
    grouped: dict[str, QueryParamGroup] = {}
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        for key, value in parse_qsl(urlparse(candidate.url).query, keep_blank_values=True):
            result_key = (key, value)
            if result_key in seen:
                continue
            seen.add(result_key)
            group = grouped.setdefault(
                key,
                QueryParamGroup(
                    key=key,
                    kind=_fallback_kind(key=key, value=value, candidate=candidate),
                    pagination_role=_fallback_pagination_role(key=key, value=value, candidate=candidate),
                    best_effort_description=f"Observed {key} parameter.",
                    confidence=0.6,
                ),
            )
            group.values.append(
                QueryParamResult(
                    value=value,
                    label=candidate.text,
                    source=candidate.source,
                    confidence=0.6 if candidate.source == "current_url" else 0.75,
                )
            )
            group.confidence = max(group.confidence, group.values[-1].confidence)
            kind = _fallback_kind(key=key, value=value, candidate=candidate)
            role = _fallback_pagination_role(key=key, value=value, candidate=candidate)
            if group.kind == "unknown" and kind != "unknown":
                group.kind = kind
            if group.pagination_role is None and role is not None:
                group.pagination_role = role
    return list(grouped.values())


def _params_from_current_candidates(
    candidates: list[QueryParamCandidate],
    cached_params: list[QueryParamGroup],
) -> list[QueryParamGroup]:
    fallback_by_key = {param.key: param for param in _fallback_params(candidates)}
    cached_by_key = {param.key: param for param in cached_params}
    params: list[QueryParamGroup] = []
    for key, fallback in fallback_by_key.items():
        cached = cached_by_key.get(key)
        if cached is None:
            params.append(fallback)
            continue
        fallback.kind = cached.kind if cached.kind != "unknown" else fallback.kind
        fallback.pagination_role = cached.pagination_role or fallback.pagination_role
        fallback.best_effort_description = cached.best_effort_description
        fallback.confidence = max(fallback.confidence, cached.confidence)
        params.append(fallback)
    return params


def _fallback_kind(*, key: str, value: str, candidate: QueryParamCandidate) -> str:
    key_lower = key.lower()
    text = (candidate.text or "").strip().lower()
    if key_lower in {"page", "p", "s", "start", "offset"} or "page" in key_lower or "token" in key_lower:
        if text in {"next", "previous", "prev"} or value.isdigit() or "next" in text or "prev" in text:
            return "pagination"
    if key_lower in {"q", "query", "keyword", "search", "p"}:
        return "text"
    if "sort" in key_lower or "order" in key_lower:
        return "sort"
    if key_lower.startswith(("min", "max")) or key_lower.endswith(("_min", "_max")):
        return "range"
    return "unknown"


def _fallback_pagination_role(*, key: str, value: str, candidate: QueryParamCandidate) -> str | None:
    key_lower = key.lower()
    text = (candidate.text or "").strip().lower()
    if key_lower in {"page", "p", "s", "start", "offset"} or "page" in key_lower or "token" in key_lower:
        if "next" in text:
            return "next"
        if "prev" in text or "previous" in text:
            return "prev"
        if value.isdigit() or text.isdigit():
            return "index"
    return None


def _fallback_query_schema(params: list[QueryParamGroup], candidates: list[QueryParamCandidate]) -> QuerySchema:
    fields: list[dict[str, str]] = []
    for param in params:
        selectors = sorted(
            {
                candidate.selector
                for candidate in candidates
                if candidate.selector
                and any(key == param.key for key, _ in parse_qsl(urlparse(candidate.url).query, keep_blank_values=True))
            }
        )
        selector = selectors[0] if selectors else f'a[href*="{_css_escape(param.key)}="]'
        fields.append(
            {
                "name": param.key,
                "selector": selector,
                "type": "attribute",
                "attribute": "href",
            }
        )

    return QuerySchema(
        schema_type="css",
        extraction_schema={
            "name": "query_params",
            "baseSelector": "body",
            "fields": fields,
        },
    )


def _schema_from_record(record) -> QuerySchema:
    return QuerySchema(schema_type=record.schema_type, extraction_schema=record.schema_json)


def _params_from_record(record) -> list[QueryParamGroup]:
    return [QueryParamGroup.model_validate(item) for item in (record.params_json or []) if isinstance(item, dict)]


def _candidates_from_record(record) -> list[QueryParamCandidate]:
    return [QueryParamCandidate.model_validate(item) for item in (record.evidence_json or []) if isinstance(item, dict)]


def cached_query_output_from_page(
    *,
    session: Session,
    page_url: str,
    html: str | None = None,
    crawl_id: UUID | None = None,
    document_id: str | None = None,
) -> QueryParamOutput | None:
    cached = find_query_schema_for_url(session, url=page_url)
    if cached is None:
        return None

    candidates = _collect_candidates(page_url, html) if html is not None else _candidates_from_record(cached)
    cached_params = _params_from_record(cached)
    return QueryParamOutput(
        url=page_url,
        crawl_id=str(crawl_id) if crawl_id else None,
        document_id=document_id,
        candidates=candidates,
        query_schema=_schema_from_record(cached),
        params=_params_from_current_candidates(candidates, cached_params) if html is not None else cached_params,
        warnings=[],
    )


def persist_query_param_output(
    session: Session,
    *,
    output: QueryParamOutput,
    task_run_id: UUID | None,
    crawl_id: UUID | None,
    document_id: str | None,
) -> None:
    if output.query_schema is None:
        return

    upsert_query_schema(
        session,
        url=output.url,
        schema_type=output.query_schema.schema_type,
        schema_json=output.query_schema.extraction_schema,
        params_json=[param.model_dump(mode="json") for param in output.params],
        evidence_json=[candidate.model_dump(mode="json") for candidate in output.candidates],
        task_run_id=task_run_id,
        crawl_id=crawl_id,
        document_id=document_id,
        inputs_json={"url": output.url},
        warnings_json={"count": len(output.warnings), "warnings": output.warnings},
    )


async def _extract_params_with_llm(
    *,
    url: str,
    candidates: list[QueryParamCandidate],
    progress_reporter: ProgressReporter | None,
) -> tuple[list[QueryParamGroup], QuerySchema]:
    await emit_progress(
        progress_reporter,
        ProgressEvent(
            resource=url,
            phase="extract_query_params",
            status="started",
            message="Generating the query-parameter schema.",
        ),
    )
    started = time.perf_counter()
    config = _llm_config()
    completion_kwargs = {"api_base": config.base_url} if config.base_url else {}
    payload = [candidate.model_dump(mode="json") for candidate in candidates]
    response = await acompletion(
        model=config.provider,
        api_key=config.api_token,
        messages=[
            {
                "role": "system",
                "content": (
                    "You identify URL query parameters from same-page navigation evidence. "
                    "Return only compact JSON. Do not invent parameters that are not present in the evidence. "
                    "Use only the DOM evidence provided. Ignore params that appear only in the page URL. "
                    "Group results by query key. Include every observed value for enum/filter keys. "
                    "Also produce a Crawl4AI JSON CSS extraction schema that can find those query parameter links again. "
                    "Use Crawl4AI field objects with name, selector, type, and attribute when extracting from href-like attributes. "
                    "Use only these kind values: text, enum, range, sort, pagination, state, unknown. "
                    "For pagination params, include pagination_role as next, prev, or index. "
                    "Use pagination_role next for Next controls, prev for Previous controls, and index for numbered pages. "
                    "Each parameter group must include key, kind, best_effort_description, confidence, and values. "
                    "Each value must include value, label, source, and confidence from 0 to 1."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Page URL: {url}\n"
                    f"Same-page URL evidence:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
                    "Return this JSON shape:\n"
                    '{ "query_schema": {'
                    '"schema_type": "css", '
                    '"extraction_schema": {'
                    '"name": "query_params", "baseSelector": "body", '
                    '"fields": ['
                    '{ "name": "category", "selector": "a[href*=\'category=\']", '
                    '"type": "attribute", "attribute": "href" }'
                    "] } }, "
                    '"params": ['
                    '{ "key": "category", "kind": "enum", "pagination_role": null, '
                    '"best_effort_description": "category filter", "confidence": 0.82, '
                    '"values": ['
                    '{ "value": "0.93", "label": "electronics", "source": "a[href]", "confidence": 0.82 }'
                    "] }"
                    "] }\n"
                    "Descriptions and labels must be very short. "
                    "If a key has many observed values in the navigation evidence, include them all. "
                    "If a pagination key has numbered values such as 1 through 9, classify it as kind pagination "
                    "with pagination_role index and include all observed numbered values. "
                    "If only Next or Previous controls are observed, classify the key as kind pagination with "
                    "pagination_role next or prev. "
                    "Prefer selectors from the evidence selector fields, generalized only enough to catch sibling controls."
                ),
            },
        ],
        temperature=0,
        max_tokens=1600,
        **completion_kwargs,
    )
    data = _extract_json(response.choices[0].message.content or "")
    raw_params = data.get("params") if isinstance(data.get("params"), list) else []
    params = [QueryParamGroup.model_validate(item) for item in raw_params if isinstance(item, dict)]
    raw_schema = data.get("query_schema") if isinstance(data.get("query_schema"), dict) else {}
    if "extraction_schema" not in raw_schema:
        if "schema" in raw_schema:
            raw_schema["extraction_schema"] = raw_schema["schema"]
        elif "schema_json" in raw_schema:
            raw_schema["extraction_schema"] = raw_schema["schema_json"]
    query_schema = QuerySchema.model_validate(raw_schema) if raw_schema else _fallback_query_schema(params, candidates)
    await emit_progress(
        progress_reporter,
        ProgressEvent(
            resource=url,
            phase="extract_query_params",
            status="succeeded",
            message=f"Extracted {len(params)} query parameters.",
            metadata={"parameters": len(params)},
            duration=time.perf_counter() - started,
        ),
    )
    return params, query_schema


async def query_from_page(
    *,
    page_url: str,
    html: str,
    crawl_id: UUID | None = None,
    document_id: str | None = None,
    progress_reporter: ProgressReporter | None = None,
    session: Session | None = None,
    task_run_id: UUID | None = None,
) -> QueryParamOutput:
    if session is not None:
        cached_output = cached_query_output_from_page(
            session=session,
            page_url=page_url,
            html=html,
            crawl_id=crawl_id,
            document_id=document_id,
        )
        if cached_output is not None:
            commit_task_checkpoint(session)
            return cached_output
        commit_task_checkpoint(session)

    await emit_progress(
        progress_reporter,
        ProgressEvent(
            resource=page_url,
            phase="collect_query_evidence",
            status="started",
            message="Collecting pagination and query evidence.",
        ),
    )
    started = time.perf_counter()
    candidates = _collect_candidates(page_url, html)
    await emit_progress(
        progress_reporter,
        ProgressEvent(
            resource=page_url,
            phase="collect_query_evidence",
            status="succeeded",
            message=f"Collected {len(candidates)} query candidates.",
            metadata={"candidates": len(candidates)},
            duration=time.perf_counter() - started,
        ),
    )

    warnings: list[str] = []
    if not candidates:
        return QueryParamOutput(
            url=page_url,
            crawl_id=str(crawl_id) if crawl_id else None,
            document_id=document_id,
            warnings=["No same-page query parameter evidence found."],
        )

    try:
        params, query_schema = await _extract_params_with_llm(
            url=_query_stripped(page_url),
            candidates=candidates,
            progress_reporter=progress_reporter,
        )
    except Exception:
        warnings.append("Schema inference unavailable; showing observed query parameters.")
        params = _fallback_params(candidates)
        query_schema = _fallback_query_schema(params, candidates)

    if session is not None:
        persist_query_param_output(
            session,
            output=QueryParamOutput(
                url=page_url,
                crawl_id=str(crawl_id) if crawl_id else None,
                document_id=document_id,
                candidates=candidates,
                query_schema=query_schema,
                params=params,
                warnings=warnings,
            ),
            task_run_id=task_run_id,
            crawl_id=crawl_id,
            document_id=document_id,
        )
        commit_task_checkpoint(session)

    return QueryParamOutput(
        url=page_url,
        crawl_id=str(crawl_id) if crawl_id else None,
        document_id=document_id,
        candidates=candidates,
        query_schema=query_schema,
        params=params,
        warnings=warnings,
    )
