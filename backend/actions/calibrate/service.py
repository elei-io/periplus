from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlunparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from actions.crawl.service import crawl_one_for_task
from actions.shared.crawl import CrawlMode, CrawlWait
from actions.shared.progress import CrawlProgressCallback, CrawlProgressEvent, emit_crawl_progress
from crawl_policies.models import CrawlPolicy
from urls.service import normalize_url, resolve_domain_url_match_for_url

from .schemas import (
    CalibrationCandidate,
    CalibrationOutput,
    CalibrationQuality,
    CalibrationTemplate,
)


@dataclass(frozen=True)
class CrawlPolicyTemplate:
    name: CalibrationTemplate
    mode: CrawlMode
    wait: CrawlWait
    run_config_overrides: dict[str, Any]


class _ContentCounter(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.link_count = 0
        self.text_parts: list[str] = []
        self._ignore_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignore_depth += 1
        if tag == "a":
            self.link_count += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignore_depth > 0:
            self._ignore_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignore_depth == 0:
            self.text_parts.append(data)

    @property
    def text_chars(self) -> int:
        text = re.sub(r"\s+", " ", " ".join(self.text_parts)).strip()
        return len(text)


_TEMPLATES: tuple[CrawlPolicyTemplate, ...] = (
    CrawlPolicyTemplate("static_fast", "static", "none", {}),
    CrawlPolicyTemplate("static_wait", "static", "stable", {}),
    CrawlPolicyTemplate("dynamic_scan", "dynamic", "none", {}),
    CrawlPolicyTemplate("app_stable", "app", "stable", {}),
    CrawlPolicyTemplate(
        "app_deep",
        "app",
        "stable",
        {
            "delay_before_return_html": 6.0,
            "max_scroll_steps": 8,
            "scroll_delay": 1.0,
        },
    ),
)


def _match_string(url_match) -> str:
    return urlunparse((url_match.scheme, url_match.host, url_match.path_pattern, "", "", ""))


def _quality(html: str, warnings: list[Any]) -> CalibrationQuality:
    counter = _ContentCounter()
    counter.feed(html or "")
    warning_codes = [
        str(getattr(warning, "code", "") or "")
        for warning in warnings
        if getattr(warning, "code", None)
    ]
    return CalibrationQuality(
        html_bytes=len((html or "").encode("utf-8")),
        text_chars=counter.text_chars,
        link_count=counter.link_count,
        warning_count=len(warnings),
        warning_codes=warning_codes,
    )


def _quality_score(quality: CalibrationQuality) -> int:
    return quality.html_bytes + (quality.text_chars * 3) + (quality.link_count * 400)


def _yield_tie_threshold(score: int) -> int:
    return max(500, int(score * 0.02))


def _template_complexity(candidate: CalibrationCandidate) -> int:
    if candidate.mode == "app":
        return 2
    if candidate.mode == "dynamic":
        return 1
    return 0


def _has_sufficient_static_signal(candidate: CalibrationCandidate) -> bool:
    return candidate.quality.link_count >= 20 or candidate.quality.text_chars >= 2500


def _meaningfully_improves_on(candidate: CalibrationCandidate, baseline: CalibrationCandidate) -> bool:
    link_gain = candidate.quality.link_count - baseline.quality.link_count
    text_gain = candidate.quality.text_chars - baseline.quality.text_chars
    return (
        link_gain >= 20
        or link_gain >= int(baseline.quality.link_count * 0.5)
        or text_gain >= 2500
        or text_gain >= int(baseline.quality.text_chars * 0.75)
    )


def _default_max_concurrency(template: CrawlPolicyTemplate) -> int:
    return 5 if template.mode == "app" else 10


def _candidate_config(template: CrawlPolicyTemplate) -> dict[str, Any]:
    return {
        "template": template.name,
        "mode": template.mode,
        "wait": template.wait,
        "max_concurrency": _default_max_concurrency(template),
        "cache_block_rules": {
            "artifact_warning_codes": [],
        },
        "run_config_overrides": template.run_config_overrides,
    }


def _policy_config(
    *,
    selected: CalibrationCandidate,
    candidates: list[CalibrationCandidate],
) -> dict[str, Any]:
    return {
        "version": 1,
        **_candidate_config(
            CrawlPolicyTemplate(
                selected.template,
                selected.mode,
                selected.wait,
                selected.run_config_overrides,
            )
        ),
        "selection": {
            "reason": selected.reason,
            "quality": selected.quality.model_dump(mode="json"),
        },
        "candidates": [
            {
                "template": candidate.template,
                "success": candidate.success,
                "accepted": candidate.accepted,
                "reason": candidate.reason,
                "duration_seconds": candidate.duration_seconds,
                "status_code": candidate.status_code,
                "quality": candidate.quality.model_dump(mode="json"),
            }
            for candidate in candidates
        ],
    }


def _select_candidate(candidates: list[CalibrationCandidate]) -> CalibrationCandidate:
    successful = [candidate for candidate in candidates if candidate.success]
    if not successful:
        return candidates[-1]

    sufficient_low_complexity = [
        candidate
        for candidate in successful
        if _template_complexity(candidate) < 2 and _has_sufficient_static_signal(candidate)
    ]
    if sufficient_low_complexity:
        baseline = max(
            sufficient_low_complexity,
            key=lambda candidate: (_quality_score(candidate.quality), -candidates.index(candidate)),
        )
        successful = [
            candidate
            for candidate in successful
            if _template_complexity(candidate) < 2 or _meaningfully_improves_on(candidate, baseline)
        ]

    best_score = max(_quality_score(candidate.quality) for candidate in successful)
    close_enough = _yield_tie_threshold(best_score)
    top_yield_candidates = [
        candidate
        for candidate in successful
        if best_score - _quality_score(candidate.quality) <= close_enough
    ]
    selected = min(
        top_yield_candidates,
        key=lambda candidate: (candidates.index(candidate), candidate.duration_seconds),
    )
    selected.accepted = True
    if sufficient_low_complexity and _template_complexity(selected) < 2:
        selected.reason = "lowest-complexity sufficient-yield template"
    elif len(top_yield_candidates) == 1:
        selected.reason = "highest-yield successful template"
    else:
        selected.reason = "cheapest template among equivalent highest-yield candidates"
    return selected


def _find_existing_policy(session: Session, *, url_match_id: UUID) -> CrawlPolicy | None:
    return session.scalar(
        select(CrawlPolicy).where(
            CrawlPolicy.enabled.is_(True),
            CrawlPolicy.url_match_id == url_match_id,
        )
    )


async def calibrate(
    *,
    url: str,
    force: bool = False,
    progress_callback: CrawlProgressCallback | None = None,
    session: Session,
    task_run_id: UUID,
) -> CalibrationOutput:
    normalized_url = normalize_url(url)
    url_match = resolve_domain_url_match_for_url(session, normalized_url, task_run_id=task_run_id)
    match = _match_string(url_match)
    existing_policy = _find_existing_policy(session, url_match_id=url_match.id)
    if existing_policy is not None and not force:
        config = existing_policy.config or {}
        selected_template = config.get("template") or "static_fast"
        return CalibrationOutput(
            url=normalized_url,
            match=match,
            url_match_id=url_match.id,
            policy=existing_policy,
            reused_policy=True,
            selected_template=selected_template,
            selected_config=config,
            candidates=[],
            selected_page=None,
        )

    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(url=normalized_url, label="calibrate", status="started"),
    )

    candidates: list[CalibrationCandidate] = []
    pages_by_template = {}
    for index, template in enumerate(_TEMPLATES):
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(url=normalized_url, label=f"test {template.name}", status="started"),
        )
        page = await crawl_one_for_task(
            url=normalized_url,
            mode=template.mode,
            wait=template.wait,
            index=index,
            progress_callback=progress_callback,
            session=session,
            task_run_id=task_run_id,
            run_config_overrides=template.run_config_overrides,
        )
        pages_by_template[template.name] = page
        quality = _quality(page.html or "", page.artifact_warnings)
        reason = "candidate succeeded" if page.success else page.error or "candidate failed"
        candidate = CalibrationCandidate(
            template=template.name,
            mode=template.mode,
            wait=template.wait,
            run_config_overrides=template.run_config_overrides,
            success=page.success,
            reason=reason,
            duration_seconds=page.duration_seconds,
            status_code=page.status_code,
            quality=quality,
            crawl_id=page.crawl_id,
            artifact_ids=page.artifact_ids,
        )
        candidates.append(candidate)
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(
                url=normalized_url,
                label=f"test {template.name}",
                status="succeeded" if page.success else "failed",
                duration=page.duration_seconds,
                error=page.error,
            ),
        )

    selected = _select_candidate(candidates)
    selected_page = pages_by_template.get(selected.template)
    config = _policy_config(selected=selected, candidates=candidates)
    policy = existing_policy or CrawlPolicy(
        url_match_id=url_match.id,
        match=match,
        config=config,
    )
    policy.url_match_id = url_match.id
    policy.match = match
    policy.enabled = True
    policy.config = config
    session.add(policy)
    session.flush()

    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(url=normalized_url, label="calibrate", status="succeeded"),
    )

    return CalibrationOutput(
        url=normalized_url,
        match=match,
        url_match_id=url_match.id,
        policy=policy,
        reused_policy=False,
        selected_template=selected.template,
        selected_config=config,
        candidates=candidates,
        selected_page=selected_page,
    )
