"""Standard-CDP page acquisition and immutable content publication."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Awaitable, Callable, TypeVar
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from sqlalchemy.orm import Session

from config import get_str
from control.crawl_policies.schemas import EffectivePolicySnapshot, ResponseOutcome
from control.urls import normalize_url
from repository.catalogue import (
    CrawlAttemptRecord,
    CrawlRecord,
    CrawlStepRecord,
    UrlRecord,
)
from repository.ingestion.acquisition import AcquisitionPipeline
from repository.objects.artifact import ArtifactIdentity
from repository.objects.html import identify_html
from runtime.context import GraphExecutionContext, graph_execution_scope
from runtime.domain_pacing import record_domain_response, wait_for_domain_interval
from runtime.resource_governor import (
    DURABLE_RESOURCE_WAIT,
    object_request,
    remote_request,
    resource_permits,
)

from .schemas import AcquisitionAttemptEvidence, CrawlPage, CrawlStepEvidence


class RetryableAcquisitionError(RuntimeError):
    def __init__(self, page: CrawlPage) -> None:
        super().__init__(page.error or "Retryable page acquisition failure")
        self.page = page
        self.retry_after_seconds = page.retry_after_seconds


class PlaywrightRuntimeLost(RuntimeError):
    """The local Playwright driver process or its transport is no longer usable."""


class ExecutionContextReplacedError(RuntimeError):
    pass


ACQUISITION_TIMEOUT_SECONDS = 120
T = TypeVar("T")


def _is_execution_context_replaced(exc: PlaywrightError) -> bool:
    detail = str(exc).lower()
    return (
        "execution context was destroyed" in detail
        or "cannot find context with specified id" in detail
    )


def _raise_if_playwright_runtime_lost(exc: PlaywrightError) -> None:
    if "connection closed while reading from the driver" in str(exc).lower():
        raise PlaywrightRuntimeLost("local Playwright driver connection was lost") from exc


async def _with_context_recovery(
    page,
    operation: Callable[[], Awaitable[T]],
    navigation,
) -> T:
    for retry in range(navigation.context_replacement_retries + 1):
        try:
            return await operation()
        except PlaywrightTimeoutError:
            raise
        except PlaywrightError as exc:
            if not _is_execution_context_replaced(exc):
                raise
            if retry >= navigation.context_replacement_retries:
                raise ExecutionContextReplacedError(str(exc)) from exc
            await page.wait_for_load_state(
                "domcontentloaded",
                timeout=navigation.timeout_ms,
            )
            if navigation.context_replacement_settle_ms:
                await page.wait_for_timeout(
                    navigation.context_replacement_settle_ms
                )
    raise AssertionError("unreachable")


async def _document_is_meaningful(page) -> bool:
    if not page.url or page.url == "about:blank":
        return False
    metrics = await _page_metrics(page)
    return metrics["elements"] > 3 and metrics["text_chars"] > 0


async def _stop_navigation(page) -> None:
    await page.evaluate("window.stop()")


async def _page_content(page) -> str:
    return await page.content()


class _MeaningfulHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements = 0
        self.text_chars = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del tag, attrs
        self.elements += 1

    def handle_data(self, data: str) -> None:
        self.text_chars += len(data.strip())


def _html_is_meaningful(html: str) -> bool:
    parser = _MeaningfulHtmlParser()
    parser.feed(html)
    return parser.elements > 3 and parser.text_chars > 0


async def _page_metrics(page) -> dict[str, int]:
    value = await page.evaluate(
        """() => ({
          elements: document.getElementsByTagName('*').length,
          textChars: (document.body?.innerText || '').trim().length,
          links: document.querySelectorAll('a[href], area[href]').length,
          scrollHeight: Math.max(
            document.documentElement?.scrollHeight || 0,
            document.body?.scrollHeight || 0
          )
        })"""
    )
    return {
        "elements": int(value.get("elements", 0)),
        "text_chars": int(value.get("textChars", 0)),
        "links": int(value.get("links", 0)),
        "scroll_height": int(value.get("scrollHeight", 0)),
    }


def _step_evidence(
    *,
    attempt_number: int,
    step_ordinal: int,
    method: str,
    config,
    started: float,
    started_at: datetime,
    before: dict[str, int],
    after: dict[str, int],
    iterations: int,
    stop_reason: str,
) -> CrawlStepEvidence:
    config_json = config.model_dump(mode="json")
    encoded = json.dumps(config_json, separators=(",", ":"), sort_keys=True)
    return CrawlStepEvidence(
        attempt_number=attempt_number,
        step_ordinal=step_ordinal,
        method=method,
        config_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        config_json=config_json,
        started_at=started_at,
        duration_ms=round((time.perf_counter() - started) * 1000),
        iterations=iterations,
        stop_reason=stop_reason,
        before_element_count=before["elements"],
        after_element_count=after["elements"],
        before_text_chars=before["text_chars"],
        after_text_chars=after["text_chars"],
        before_link_count=before["links"],
        after_link_count=after["links"],
        before_scroll_height=before["scroll_height"],
        after_scroll_height=after["scroll_height"],
    )


async def _wait_dynamic(
    page, config, *, attempt_number: int, step_ordinal: int
) -> CrawlStepEvidence:
    started = time.perf_counter()
    started_at = datetime.now(UTC)
    before = await _page_metrics(page)
    previous = before
    stable = 0
    iterations = 0
    stop_reason = "deadline"
    while (time.perf_counter() - started) * 1000 < config.maximum_wait_ms:
        await page.wait_for_timeout(config.sample_interval_ms)
        current = await _page_metrics(page)
        iterations += 1
        meaningful = current["elements"] > 3 and current["text_chars"] > 0
        stable = stable + 1 if current == previous and meaningful else 0
        previous = current
        if stable >= config.stable_samples:
            stop_reason = "stable"
            break
    return _step_evidence(
        attempt_number=attempt_number,
        step_ordinal=step_ordinal,
        method="wait_dynamic",
        config=config,
        started=started,
        started_at=started_at,
        before=before,
        after=previous,
        iterations=iterations,
        stop_reason=stop_reason,
    )


async def _wait_fixed(
    page, config, *, attempt_number: int, step_ordinal: int
) -> CrawlStepEvidence:
    started = time.perf_counter()
    started_at = datetime.now(UTC)
    before = await _page_metrics(page)
    await page.wait_for_timeout(config.duration_ms)
    after = await _page_metrics(page)
    return _step_evidence(
        attempt_number=attempt_number,
        step_ordinal=step_ordinal,
        method="wait_fixed",
        config=config,
        started=started,
        started_at=started_at,
        before=before,
        after=after,
        iterations=1,
        stop_reason="fixed_duration_elapsed",
    )


async def _scroll(
    page, config, *, attempt_number: int, step_ordinal: int
) -> CrawlStepEvidence:
    started = time.perf_counter()
    started_at = datetime.now(UTC)
    before = await _page_metrics(page)
    previous = before
    stable_bottoms = 0
    stop_reason = "budget_exhausted"
    iterations = 0
    for iterations in range(1, config.maximum_iterations + 1):
        at_bottom = await page.evaluate(
            r"""(viewportRatio) => {
              const root = document.scrollingElement || document.documentElement;
              const viewport = Math.max(window.innerHeight || 0, 1);
              root.scrollBy(0, Math.max(1, Math.floor(viewport * viewportRatio)));
              return root.scrollTop + viewport >= root.scrollHeight - 2;
            }""",
            config.viewport_ratio,
        )
        if config.wait_ms:
            await page.wait_for_timeout(config.wait_ms)
        current = await _page_metrics(page)
        stable_bottoms = stable_bottoms + 1 if at_bottom and current == previous else 0
        previous = current
        if stable_bottoms >= config.stable_bottom_samples:
            stop_reason = "bottom_stable"
            break
    return _step_evidence(
        attempt_number=attempt_number,
        step_ordinal=step_ordinal,
        method="scroll",
        config=config,
        started=started,
        started_at=started_at,
        before=before,
        after=previous,
        iterations=iterations,
        stop_reason=stop_reason,
    )


async def _expand(
    page, config, *, attempt_number: int, step_ordinal: int
) -> CrawlStepEvidence:
    started = time.perf_counter()
    started_at = datetime.now(UTC)
    before = await _page_metrics(page)
    after = before
    stop_reason = "no_control"
    iterations = 0
    for iterations in range(1, config.maximum_actions + 1):
        clicked = await page.evaluate(
            r"""() => {
              const label = /^(load|show|view)\s+more(?:\s+(?:items|results|products|posts|comments))?$|^more$/i;
              const candidates = [...document.querySelectorAll('button, [role="button"]')];
              const target = candidates.find((element) => {
                const rect = element.getBoundingClientRect();
                const text = (element.innerText || element.getAttribute('aria-label') || '').trim();
                return label.test(text) && rect.width > 0 && rect.height > 0 && !element.disabled;
              });
              if (!target) return false;
              target.click();
              return true;
            }"""
        )
        if not clicked:
            iterations -= 1
            break
        if config.wait_ms:
            await page.wait_for_timeout(config.wait_ms)
        current = await _page_metrics(page)
        if current == after:
            stop_reason = "no_growth"
            after = current
            break
        after = current
        stop_reason = (
            "budget_exhausted"
            if iterations == config.maximum_actions
            else "expanded"
        )
    return _step_evidence(
        attempt_number=attempt_number,
        step_ordinal=step_ordinal,
        method="expand",
        config=config,
        started=started,
        started_at=started_at,
        before=before,
        after=after,
        iterations=iterations,
        stop_reason=stop_reason,
    )


def _status_outcome(status: int | None, policy) -> ResponseOutcome:
    if status is None:
        return "accept"
    for rule in policy.content.response_rules.http_status:
        if rule.matches(status):
            return rule.outcome
    return "accept"


def _media_type(value: str | None) -> str:
    return (value or "text/html").partition(";")[0].strip().lower()


def _accepted_media_type(media_type: str, accepted: tuple[str, ...]) -> bool:
    return any(value == media_type or (value.endswith("/*") and media_type.startswith(value[:-1])) for value in accepted)


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return float(stripped)
    try:
        parsed = parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return None
    return max(0.0, (parsed - datetime.now(UTC)).total_seconds())


def _attempt(
    *,
    number: int,
    started_at: datetime,
    requested_url: str,
    final_url: str | None,
    status_code: int | None,
    media_type: str | None,
    outcome: str,
    failure_code: str | None = None,
    retry_after_seconds: float | None = None,
) -> AcquisitionAttemptEvidence:
    return AcquisitionAttemptEvidence(
        attempt=number,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        requested_url=requested_url,
        final_url=final_url,
        status_code=status_code,
        response_media_type=media_type,
        outcome=outcome,
        failure_code=failure_code,
        retry_after_seconds=retry_after_seconds,
    )


async def _acquire(
    url: str,
    policy,
    *,
    attempt_number: int,
    playwright: Playwright,
) -> CrawlPage:
    started = time.perf_counter()
    started_at = datetime.now(UTC)
    browser = None
    try:
        async with asyncio.timeout(ACQUISITION_TIMEOUT_SECONDS):
            try:
                browser = await playwright.chromium.connect_over_cdp(get_str("CDP_URL"))
            except PlaywrightTimeoutError as exc:
                return _failed_page(url, started, started_at, attempt_number, str(exc) or "CDP connection timed out", "cdp_connection_timeout", "connection", True)
            except PlaywrightError as exc:
                _raise_if_playwright_runtime_lost(exc)
                return _failed_page(url, started, started_at, attempt_number, str(exc), "cdp_connection_failed", "connection", True)
            except OSError as exc:
                return _failed_page(url, started, started_at, attempt_number, str(exc), "cdp_connection_failed", "connection", True)
            try:
                completion = policy.content.completion
                # A static policy is expressed by omitting browser-only CDP
                # operations. Explicitly disabling script execution is itself
                # such an operation and would force a lazy HTTP CDP facade to
                # promote the request to a browser.
                page = await browser.new_page()
                steps: list[CrawlStepEvidence] = []
                status: int | None = None
                media_type = "text/html"
                final_url = page.url
                html: str | None = None
                navigation_timed_out = False
                navigation_timeout_detail = ""
                try:
                    response = await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=completion.navigation.timeout_ms,
                    )
                except PlaywrightTimeoutError as exc:
                    navigation_timed_out = True
                    navigation_timeout_detail = (
                        str(exc) or "Page navigation timed out"
                    )
                    if completion.browser_interaction_enabled:
                        meaningful = await _with_context_recovery(
                            page,
                            lambda: _document_is_meaningful(page),
                            completion.navigation,
                        )
                    elif not page.url or page.url == "about:blank":
                        meaningful = False
                    else:
                        html = await page.content()
                        meaningful = _html_is_meaningful(html)
                    if not meaningful:
                        return _failed_page(
                            url,
                            started,
                            started_at,
                            attempt_number,
                            navigation_timeout_detail,
                            "navigation_timeout",
                            "navigation",
                            True,
                        )
                    if completion.browser_interaction_enabled:
                        await _with_context_recovery(
                            page,
                            lambda: _stop_navigation(page),
                            completion.navigation,
                        )
                    response = None
                status = response.status if response is not None else None
                final_url = page.url
                content_type = await response.header_value("content-type") if response is not None else None
                media_type = _media_type(content_type)
                retry_after = _retry_after(await response.header_value("retry-after") if response is not None else None)
                outcome = _status_outcome(status, policy)
                if outcome != "accept":
                    await browser.close()
                    browser = None
                    return _response_outcome_page(url=final_url, requested_url=url, started=started, started_at=started_at, attempt_number=attempt_number, status=status, media_type=media_type, outcome=outcome, failure_code="http_status", retry_after=retry_after)
                if not _accepted_media_type(media_type, policy.content.accepted_content_types):
                    outcome = policy.content.response_rules.unsupported_content_type
                    if outcome != "accept":
                        await browser.close()
                        browser = None
                        return _response_outcome_page(url=final_url, requested_url=url, started=started, started_at=started_at, attempt_number=attempt_number, status=status, media_type=media_type, outcome=outcome, failure_code="unsupported_content_type")
                if media_type not in {"text/html", "application/xhtml+xml"}:
                    artifact = await response.body() if response is not None else b""
                    evidence = _attempt(number=attempt_number, started_at=started_at, requested_url=url, final_url=final_url, status_code=status, media_type=media_type, outcome="success")
                    await browser.close()
                    browser = None
                    return CrawlPage(url=final_url, success=True, status_code=status, duration_seconds=time.perf_counter() - started, outcome="success", response_media_type=media_type, artifact=artifact, attempt_evidence=evidence)
                if completion.wait_dynamic.enabled:
                    steps.append(
                        await _with_context_recovery(
                            page,
                            lambda: _wait_dynamic(
                                page,
                                completion.wait_dynamic,
                                attempt_number=attempt_number,
                                step_ordinal=len(steps) + 1,
                            ),
                            completion.navigation,
                        )
                    )
                if completion.wait_fixed.enabled:
                    steps.append(
                        await _with_context_recovery(
                            page,
                            lambda: _wait_fixed(
                                page,
                                completion.wait_fixed,
                                attempt_number=attempt_number,
                                step_ordinal=len(steps) + 1,
                            ),
                            completion.navigation,
                        )
                    )
                if completion.scroll.enabled:
                    steps.append(
                        await _with_context_recovery(
                            page,
                            lambda: _scroll(
                                page,
                                completion.scroll,
                                attempt_number=attempt_number,
                                step_ordinal=len(steps) + 1,
                            ),
                            completion.navigation,
                        )
                    )
                if completion.expand.enabled:
                    steps.append(
                        await _with_context_recovery(
                            page,
                            lambda: _expand(
                                page,
                                completion.expand,
                                attempt_number=attempt_number,
                                step_ordinal=len(steps) + 1,
                            ),
                            completion.navigation,
                        )
                    )
                if (
                    completion.scroll.enabled
                    and steps
                    and steps[-1].method == "expand"
                    and steps[-1].changed
                ):
                    steps.append(
                        await _with_context_recovery(
                            page,
                            lambda: _scroll(
                                page,
                                completion.scroll,
                                attempt_number=attempt_number,
                                step_ordinal=len(steps) + 1,
                            ),
                            completion.navigation,
                        )
                    )
                if html is None:
                    html = await _with_context_recovery(
                        page,
                        lambda: _page_content(page),
                        completion.navigation,
                    )
                final_url = page.url
                if navigation_timed_out and not html.strip():
                    return _failed_page(
                        url,
                        started,
                        started_at,
                        attempt_number,
                        navigation_timeout_detail,
                        "navigation_timeout",
                        "navigation",
                        True,
                    )
            except ExecutionContextReplacedError as exc:
                try:
                    html = await page.content()
                except PlaywrightError:
                    return _failed_page(url, started, started_at, attempt_number, str(exc), "execution_context_replaced", "completion", True)
                if not _html_is_meaningful(html):
                    return _failed_page(url, started, started_at, attempt_number, str(exc), "execution_context_replaced", "completion", True)
                final_url = page.url
            except PlaywrightTimeoutError as exc:
                return _failed_page(url, started, started_at, attempt_number, str(exc) or "Page navigation timed out", "navigation_timeout", "navigation", True)
            except PlaywrightError as exc:
                _raise_if_playwright_runtime_lost(exc)
                return _failed_page(url, started, started_at, attempt_number, str(exc), "navigation_failed", "navigation", True)
            await browser.close()
            browser = None
        evidence = _attempt(number=attempt_number, started_at=started_at, requested_url=url, final_url=final_url, status_code=status, media_type=media_type, outcome="success")
        return CrawlPage(url=final_url, success=True, status_code=status, duration_seconds=time.perf_counter() - started, html=html, outcome="success", response_media_type=media_type, attempt_evidence=evidence, steps=tuple(steps))
    except TimeoutError as exc:
        return _failed_page(url, started, started_at, attempt_number, str(exc) or "Page acquisition timed out", "acquisition_timeout", "acquisition", True)
    except PlaywrightError as exc:
        _raise_if_playwright_runtime_lost(exc)
        raise
    finally:
        if browser is not None:
            try:
                await browser.close()
            except PlaywrightError as exc:
                _raise_if_playwright_runtime_lost(exc)


def _response_outcome_page(*, url: str, requested_url: str, started: float, started_at: datetime, attempt_number: int, status: int | None, media_type: str, outcome: ResponseOutcome, failure_code: str, retry_after: float | None = None) -> CrawlPage:
    if outcome == "skip":
        evidence = _attempt(number=attempt_number, started_at=started_at, requested_url=requested_url, final_url=url, status_code=status, media_type=media_type, outcome="skipped", failure_code=failure_code)
        return CrawlPage(url=url, success=True, status_code=status, duration_seconds=time.perf_counter() - started, outcome="skipped", response_media_type=media_type, attempt_evidence=evidence)
    retryable = outcome == "retry"
    evidence = _attempt(number=attempt_number, started_at=started_at, requested_url=requested_url, final_url=url, status_code=status, media_type=media_type, outcome="retry" if retryable else "failed", failure_code=failure_code, retry_after_seconds=retry_after)
    detail = f"Page returned HTTP {status}" if failure_code == "http_status" else failure_code.replace("_", " ").capitalize()
    return CrawlPage(url=url, success=False, status_code=status, duration_seconds=time.perf_counter() - started, error=detail, failure_code=failure_code, failure_stage="navigation", failure_retryable=retryable, retry_after_seconds=retry_after, outcome="failed", response_media_type=media_type, attempt_evidence=evidence)


def _failed_page(url: str, started: float, started_at: datetime, attempt_number: int, error: str, code: str, stage: str, retryable: bool) -> CrawlPage:
    evidence = _attempt(number=attempt_number, started_at=started_at, requested_url=url, final_url=None, status_code=None, media_type=None, outcome="retry" if retryable else "failed", failure_code=code)
    return CrawlPage(url=url, success=False, duration_seconds=time.perf_counter() - started, error=error, failure_code=code, failure_stage=stage, failure_retryable=retryable, outcome="failed", attempt_evidence=evidence)


async def crawl_graph_request(
    *,
    session: Session | None,
    url: str,
    context: GraphExecutionContext,
    playwright: Playwright,
    repository_pipeline: AcquisitionPipeline | None = None,
    resource_grants=None,
    domain_pacing=None,
    domain_permit=None,
    persist_retryable_failure: bool = True,
    include_html: bool = False,
    **_kwargs,
) -> CrawlPage:
    del session
    effective = EffectivePolicySnapshot.model_validate(context.effective_policy_snapshot_json)
    policy = effective.crawl
    domain = effective.domain
    policy_json_value = effective.model_dump(mode="json")
    policy_json = json.dumps(policy_json_value, separators=(",", ":"), sort_keys=True)
    normalized = normalize_url(url)
    remote_domain = (urlparse(normalized).hostname or "unknown").lower()
    attempt_number = len(context.prior_attempts_json) + 1
    with graph_execution_scope(context):
        if domain_permit is not None:
            async with domain_permit:
                if domain_pacing is not None:
                    await wait_for_domain_interval(domain_pacing, domain=remote_domain, interval_seconds=domain.minimum_request_interval_seconds)
                page = await _acquire(
                    normalized,
                    policy,
                    attempt_number=attempt_number,
                    playwright=playwright,
                )
        elif resource_grants is None:
            if domain_pacing is not None:
                await wait_for_domain_interval(domain_pacing, domain=remote_domain, interval_seconds=domain.minimum_request_interval_seconds)
            page = await _acquire(
                normalized,
                policy,
                attempt_number=attempt_number,
                playwright=playwright,
            )
        else:
            async with resource_permits(resource_grants, remote_request(f"domain:{context.crawl_request_id}:{attempt_number}", remote_domain=remote_domain, concurrency=domain.maximum_concurrency), acquire_timeout=DURABLE_RESOURCE_WAIT):
                if domain_pacing is not None:
                    await wait_for_domain_interval(domain_pacing, domain=remote_domain, interval_seconds=domain.minimum_request_interval_seconds)
                page = await _acquire(
                    normalized,
                    policy,
                    attempt_number=attempt_number,
                    playwright=playwright,
                )
        if domain_pacing is not None:
            try:
                await record_domain_response(
                    domain_pacing,
                    domain=remote_domain,
                    status_code=page.status_code,
                    retry_after_seconds=page.retry_after_seconds,
                )
            except Exception:
                logging.warning(
                    "failed to record adaptive domain pacing for %s",
                    remote_domain,
                    exc_info=True,
                )
        if not page.success and page.failure_retryable and not persist_retryable_failure:
            raise RetryableAcquisitionError(page)
        identity = identify_html(page.html) if page.html is not None and page.success else None
        artifact_identity = ArtifactIdentity(sha256=hashlib.sha256(page.artifact).hexdigest(), size_bytes=len(page.artifact)) if page.artifact is not None and page.success else None
        attempt_evidence = tuple(
            AcquisitionAttemptEvidence.model_validate(value)
            for value in context.prior_attempts_json
        ) + ((page.attempt_evidence,) if page.attempt_evidence is not None else ())
        requested_url = UrlRecord.from_normalized_url(normalized)
        final_normalized = normalize_url(page.url) if page.url else None
        final_url = (
            UrlRecord.from_normalized_url(final_normalized)
            if final_normalized is not None
            else None
        )
        urls = tuple(
            {value.url_id: value for value in (requested_url, final_url) if value is not None}.values()
        )
        crawl_attempts = tuple(
            CrawlAttemptRecord(
                crawl_id=context.crawl_request_id,
                attempt_number=attempt.attempt,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
                requested_url_id=UrlRecord.from_normalized_url(
                    normalize_url(attempt.requested_url)
                ).url_id,
                final_url_id=(
                    UrlRecord.from_normalized_url(
                        normalize_url(attempt.final_url)
                    ).url_id
                    if attempt.final_url is not None
                    else None
                ),
                status_code=attempt.status_code,
                response_media_type=attempt.response_media_type,
                outcome=attempt.outcome,
                failure_code=attempt.failure_code,
                retry_after_seconds=attempt.retry_after_seconds,
            )
            for attempt in attempt_evidence
        )
        attempt_urls = tuple(
            UrlRecord.from_normalized_url(normalize_url(value))
            for attempt in attempt_evidence
            for value in (attempt.requested_url, attempt.final_url)
            if value is not None
        )
        urls = tuple(
            {
                value.url_id: value
                for value in (*urls, *attempt_urls)
            }.values()
        )
        record = CrawlRecord(
            crawl_id=context.crawl_request_id,
            document_id=identity.document_id if identity else None,
            artifact_id=artifact_identity.artifact_id if artifact_identity else None,
            graph_id=context.graph_id,
            graph_run_id=context.graph_run_id,
            graph_node_id=context.graph_node_id,
            crawl_request_id=context.crawl_request_id,
            source_crawl_id=context.source_crawl_id,
            source_edge_id=context.source_edge_id,
            requested_url_id=requested_url.url_id,
            final_url_id=final_url.url_id if final_url is not None else None,
            captured_at=datetime.now(UTC),
            status_code=page.status_code,
            duration_ms=round(page.duration_seconds * 1000),
            response_media_type=page.response_media_type,
            policy_config_hash=hashlib.sha256(policy_json.encode()).hexdigest(),
            policy_config_json=policy_json_value,
            crawl_policy_id=policy.id,
            outcome=page.outcome,
            failure_code=page.failure_code if not page.success else None,
            failure_stage=page.failure_stage if not page.success else None,
            failure_retryable=page.failure_retryable if not page.success else None,
            failure_detail=page.error if not page.success else None,
        )
        crawl_steps = tuple(
            CrawlStepRecord(
                crawl_id=context.crawl_request_id,
                **step.model_dump(mode="python"),
            )
            for step in page.steps
        )

        async def persist(pipeline: AcquisitionPipeline) -> CrawlPage:
            if identity is not None:
                request = object_request(f"raw-html:{context.crawl_request_id}", direction="write", byte_count=identity.size_bytes, service_class="critical")
                if resource_grants is None:
                    await pipeline.store_raw(captured_html=page.html or "", identity=identity)
                else:
                    async with resource_permits(resource_grants, request, acquire_timeout=DURABLE_RESOURCE_WAIT):
                        await pipeline.store_raw(captured_html=page.html or "", identity=identity)
            if artifact_identity is not None:
                request = object_request(f"raw-artifact:{context.crawl_request_id}", direction="write", byte_count=artifact_identity.size_bytes, service_class="critical")
                if resource_grants is None:
                    await pipeline.store_artifact(content=io.BytesIO(page.artifact or b""), identity=artifact_identity)
                else:
                    async with resource_permits(resource_grants, request, acquire_timeout=DURABLE_RESOURCE_WAIT):
                        await pipeline.store_artifact(content=io.BytesIO(page.artifact or b""), identity=artifact_identity)
            await pipeline.enqueue_stored(
                record,
                urls=urls,
                crawl_attempts=crawl_attempts,
                crawl_steps=crawl_steps,
            )
            return page.model_copy(
                update={
                    "crawl_id": context.crawl_request_id,
                    "document_id": record.document_id,
                    "html": page.html if include_html else None,
                    "artifact": None,
                }
            )

        if repository_pipeline is not None:
            return await persist(repository_pipeline)
        async with AcquisitionPipeline() as pipeline:
            return await persist(pipeline)
