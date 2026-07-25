"""Standard-CDP page acquisition and immutable content publication."""

from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import suppress
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import hashlib
import io
import json
import logging
import re
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
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
    NormalizedUrl,
)
from repository.catalogue.schema import POLICY_SCHEMA_VERSION
from repository.ingestion.acquisition import AcquisitionPipeline
from repository.objects.artifact import ArtifactIdentity
from repository.objects.html import identify_html
from runtime.context import GraphExecutionContext, graph_execution_scope
from runtime.domain_pacing import (
    domain_permit as acquire_domain_permit,
    record_domain_response,
    wait_for_domain_interval,
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


def _is_download_navigation(exc: PlaywrightError) -> bool:
    return "download is starting" in str(exc).lower()


async def _cancel_event_wait(task: asyncio.Task) -> None:
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError, PlaywrightError):
        await task


def _is_navigation_response(response) -> bool:
    try:
        return response.request.is_navigation_request()
    except PlaywrightError:
        return False


async def _final_redirect_response(response):
    request = response.request
    while request.redirected_to is not None:
        request = request.redirected_to
        redirected_response = await request.response()
        if redirected_response is not None:
            response = redirected_response
    return response


async def _download_bytes(download) -> bytes:
    """Copy a remote-CDP download through Playwright before its context closes."""
    with tempfile.NamedTemporaryFile(prefix="atlas-download-") as target:
        await download.save_as(target.name)
        failure = await download.failure()
        if failure is not None:
            raise PlaywrightError(failure)
        return await asyncio.to_thread(Path(target.name).read_bytes)


class _NavigationArtifactCapture:
    """Capture a document response before Chromium hands it to a MIME viewer."""

    def __init__(self, accepted_content_types: tuple[str, ...]) -> None:
        self.accepted_content_types = accepted_content_types
        self.body: bytes | None = None
        self.error: str | None = None

    async def handle_paused(self, session, event: dict[str, object]) -> None:
        request_id = str(event["requestId"])
        try:
            status = event.get("responseStatusCode")
            headers = {
                str(entry.get("name", "")).lower(): str(entry.get("value", ""))
                for entry in event.get("responseHeaders", [])
                if isinstance(entry, dict)
            }
            media_type = _media_type(headers.get("content-type"))
            disposition = headers.get("content-disposition", "").lower()
            is_redirect = status in {301, 302, 303, 307, 308}
            should_capture = (
                not is_redirect
                and "attachment" not in disposition
                and media_type not in {"text/html", "application/xhtml+xml"}
                and _accepted_media_type(
                    media_type,
                    self.accepted_content_types,
                )
            )
            if should_capture:
                response = await session.send(
                    "Fetch.getResponseBody",
                    {"requestId": request_id},
                )
                encoded = str(response.get("body", ""))
                self.body = (
                    base64.b64decode(encoded, validate=True)
                    if response.get("base64Encoded")
                    else encoded.encode()
                )
        except (binascii.Error, ValueError, PlaywrightError) as exc:
            self.error = str(exc) or "raw navigation response capture failed"
        finally:
            await session.send(
                "Fetch.continueRequest",
                {"requestId": request_id},
            )


async def _enable_navigation_artifact_capture(
    page,
    accepted_content_types: tuple[str, ...],
) -> _NavigationArtifactCapture | None:
    if not any(
        media_type not in {"text/html", "application/xhtml+xml"}
        for media_type in accepted_content_types
    ):
        return None
    session = await page.context.new_cdp_session(page)
    capture = _NavigationArtifactCapture(accepted_content_types)
    session.on(
        "Fetch.requestPaused",
        lambda event: capture.handle_paused(session, event),
    )
    await session.send(
        "Fetch.enable",
        {
            "patterns": [
                {
                    "urlPattern": "*",
                    "resourceType": "Document",
                    "requestStage": "Response",
                }
            ]
        },
    )
    return capture


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
                artifact_capture = await _enable_navigation_artifact_capture(
                    page,
                    policy.content.accepted_content_types,
                )
                steps: list[CrawlStepEvidence] = []
                status: int | None = None
                media_type = "text/html"
                final_url = page.url
                html: str | None = None
                navigation_timed_out = False
                navigation_timeout_detail = ""
                download_wait = asyncio.create_task(
                    page.wait_for_event(
                        "download",
                        timeout=completion.navigation.timeout_ms,
                    )
                )
                response_wait = asyncio.create_task(
                    page.wait_for_event(
                        "response",
                        predicate=_is_navigation_response,
                        timeout=completion.navigation.timeout_ms,
                    )
                )
                try:
                    response = await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=completion.navigation.timeout_ms,
                    )
                except PlaywrightTimeoutError as exc:
                    await _cancel_event_wait(download_wait)
                    await _cancel_event_wait(response_wait)
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
                except PlaywrightError as exc:
                    if not _is_download_navigation(exc):
                        await _cancel_event_wait(download_wait)
                        await _cancel_event_wait(response_wait)
                        raise
                    try:
                        download, response = await asyncio.gather(
                            download_wait,
                            response_wait,
                        )
                        response = await _final_redirect_response(response)
                    except PlaywrightError as download_exc:
                        await _cancel_event_wait(download_wait)
                        await _cancel_event_wait(response_wait)
                        return _failed_page(
                            url,
                            started,
                            started_at,
                            attempt_number,
                            str(download_exc),
                            "download_failed",
                            "navigation",
                            True,
                        )
                    status = response.status
                    final_url = response.url or download.url or url
                    content_type = await response.header_value("content-type")
                    media_type = _media_type(content_type)
                    retry_after = _retry_after(
                        await response.header_value("retry-after")
                    )
                    outcome = _status_outcome(status, policy)
                    if outcome != "accept":
                        await download.cancel()
                        return _response_outcome_page(
                            url=final_url,
                            requested_url=url,
                            started=started,
                            started_at=started_at,
                            attempt_number=attempt_number,
                            status=status,
                            media_type=media_type,
                            outcome=outcome,
                            failure_code="http_status",
                            retry_after=retry_after,
                        )
                    if not _accepted_media_type(
                        media_type, policy.content.accepted_content_types
                    ):
                        outcome = (
                            policy.content.response_rules.unsupported_content_type
                        )
                        if outcome != "accept":
                            await download.cancel()
                            return _response_outcome_page(
                                url=final_url,
                                requested_url=url,
                                started=started,
                                started_at=started_at,
                                attempt_number=attempt_number,
                                status=status,
                                media_type=media_type,
                                outcome=outcome,
                                failure_code="unsupported_content_type",
                            )
                    try:
                        artifact = await _download_bytes(download)
                    except (OSError, PlaywrightError) as download_exc:
                        return _failed_page(
                            url,
                            started,
                            started_at,
                            attempt_number,
                            str(download_exc),
                            "download_failed",
                            "acquisition",
                            True,
                        )
                    evidence = _attempt(
                        number=attempt_number,
                        started_at=started_at,
                        requested_url=url,
                        final_url=final_url,
                        status_code=status,
                        media_type=media_type,
                        outcome="success",
                    )
                    return CrawlPage(
                        url=final_url,
                        success=True,
                        status_code=status,
                        duration_seconds=time.perf_counter() - started,
                        outcome="success",
                        response_media_type=media_type,
                        artifact=artifact,
                        attempt_evidence=evidence,
                    )
                else:
                    await _cancel_event_wait(download_wait)
                    await _cancel_event_wait(response_wait)
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
                    if artifact_capture is None:
                        raise AssertionError(
                            "non-HTML response was accepted without raw capture"
                        )
                    if artifact_capture.error is not None:
                        return _failed_page(
                            url,
                            started,
                            started_at,
                            attempt_number,
                            artifact_capture.error,
                            "artifact_capture_failed",
                            "acquisition",
                            True,
                        )
                    if artifact_capture.body is None:
                        return _failed_page(
                            url,
                            started,
                            started_at,
                            attempt_number,
                            "raw navigation response body was not captured",
                            "artifact_capture_failed",
                            "acquisition",
                            True,
                        )
                    artifact = artifact_capture.body
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
        elif domain_pacing is not None:
            async with acquire_domain_permit(
                domain_pacing,
                domain=remote_domain,
                concurrency=domain.maximum_concurrency,
            ):
                if domain_pacing is not None:
                    await wait_for_domain_interval(domain_pacing, domain=remote_domain, interval_seconds=domain.minimum_request_interval_seconds)
                page = await _acquire(
                    normalized,
                    policy,
                    attempt_number=attempt_number,
                    playwright=playwright,
                )
        else:
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
        requested_url = NormalizedUrl.from_normalized_url(normalized)
        final_normalized = normalize_url(page.url) if page.url else None
        final_url = (
            NormalizedUrl.from_normalized_url(final_normalized)
            if final_normalized is not None
            else None
        )
        effective_url = final_url or requested_url
        crawl_attempts = tuple(
            CrawlAttemptRecord(
                crawl_id=context.crawl_request_id,
                attempt_number=attempt.attempt,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
                requested_url=normalize_url(attempt.requested_url),
                url=(
                    normalize_url(attempt.final_url)
                    if attempt.final_url is not None
                    else normalize_url(attempt.requested_url)
                ),
                status_code=attempt.status_code,
                response_media_type=attempt.response_media_type,
                outcome=attempt.outcome,
                failure_code=attempt.failure_code,
                retry_after_seconds=attempt.retry_after_seconds,
            )
            for attempt in attempt_evidence
        )
        if not crawl_attempts:
            raise RuntimeError("crawl acquisition produced no typed attempt evidence")
        completed_at = datetime.now(UTC)
        content_captured_at = (
            completed_at
            if identity is not None or artifact_identity is not None
            else None
        )
        record = CrawlRecord(
            crawl_id=context.crawl_request_id,
            document_id=identity.document_id if identity else None,
            artifact_id=artifact_identity.artifact_id if artifact_identity else None,
            graph_id=context.graph_id,
            graph_run_id=context.graph_run_id,
            graph_node_id=context.graph_node_id,
            source_crawl_id=context.source_crawl_id,
            source_edge_id=context.source_edge_id,
            requested_url=normalized,
            url=effective_url.normalized_url,
            scheme=effective_url.scheme,
            host=effective_url.host,
            port=effective_url.port,
            registrable_domain=effective_url.registrable_domain,
            path=effective_url.path,
            query=effective_url.query,
            started_at=crawl_attempts[0].started_at,
            completed_at=completed_at,
            content_captured_at=content_captured_at,
            status_code=page.status_code,
            response_media_type=page.response_media_type,
            policy_schema_version=POLICY_SCHEMA_VERSION,
            effective_policy_hash=hashlib.sha256(policy_json.encode()).hexdigest(),
            effective_policy=policy_json_value,
            outcome=page.outcome,
            failure_code=page.failure_code if page.outcome == "failed" else None,
            failure_stage=page.failure_stage if page.outcome == "failed" else None,
            failure_retryable=page.failure_retryable if page.outcome == "failed" else None,
            failure_detail=page.error if page.outcome == "failed" else None,
        )
        crawl_steps = tuple(
            CrawlStepRecord(
                crawl_id=context.crawl_request_id,
                **step.model_dump(mode="python"),
            )
            for step in page.steps
        )

        async def persist(pipeline: AcquisitionPipeline) -> CrawlPage:
            source_url = final_normalized or normalized
            if identity is not None:
                assert content_captured_at is not None
                await pipeline.store_raw(
                    captured_html=page.html or "",
                    source_url=source_url,
                    crawl_id=context.crawl_request_id,
                    captured_at=content_captured_at,
                    content_type=page.response_media_type or "text/html",
                    identity=identity,
                )
            if artifact_identity is not None:
                assert content_captured_at is not None
                await pipeline.store_artifact(
                    content=io.BytesIO(page.artifact or b""),
                    identity=artifact_identity,
                    source_url=source_url,
                    crawl_id=context.crawl_request_id,
                    captured_at=content_captured_at,
                    content_type=page.response_media_type or "application/octet-stream",
                )
            await pipeline.enqueue_stored(
                record,
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
