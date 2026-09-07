"""Acquire one response through the configured standard CDP endpoint."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import tempfile
import time
from contextlib import suppress, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Playwright, Browser
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from periplus.crawl.control.collections.exclusions import UrlExclusion
from periplus.crawl.acquisition.errors import ExecutionContextReplacedError, PlaywrightRuntimeLost, CdpUnavailable
from periplus.crawl.acquisition.models import AcquisitionResult, AcquisitionStepEvidence
from periplus.crawl.acquisition.readiness import (
    document_is_meaningful,
    expand,
    html_is_meaningful,
    page_content,
    scroll,
    stop_navigation,
    wait_dynamic,
    wait_fixed,
    with_context_recovery,
)
from periplus.crawl.acquisition.responses import (
    accepted_media_type,
    acquisition_failure,
    attempt_evidence,
    parse_media_type,
    parse_retry_after,
    policy_rejection,
)
from periplus.platform.config import get_str

ACQUISITION_TIMEOUT_SECONDS = 120


def _raise_if_playwright_runtime_lost(exc: PlaywrightError) -> None:
    if "connection closed while reading from the driver" in str(exc).lower():
        raise PlaywrightRuntimeLost(
            "local Playwright driver connection was lost"
        ) from exc


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
    with tempfile.NamedTemporaryFile(prefix="periplus-download-") as target:
        await download.save_as(target.name)
        failure = await download.failure()
        if failure is not None:
            raise PlaywrightError(failure)
        return await asyncio.to_thread(Path(target.name).read_bytes)


class _NavigationArtifactCapture:
    """Capture a document response before Chromium hands it to a MIME viewer."""

    def __init__(self, accepted_content_types: tuple[str, ...], exclusions: tuple[UrlExclusion, ...] = ()) -> None:
        self.accepted_content_types = accepted_content_types
        self.exclusions = exclusions
        self.blocked_document = False
        self.main_frame_id: str | None = None
        self.body: bytes | None = None
        self.error: str | None = None

    async def handle_paused(self, session, event: dict[str, object]) -> None:
        request_id = str(event["requestId"])
        if "responseStatusCode" not in event and "responseErrorReason" not in event:
            url = str(event.get("request", {}).get("url", ""))
            if any(rule.matches(url) for rule in self.exclusions):
                if event.get("resourceType") == "Document" and event.get("frameId") == self.main_frame_id:
                    self.blocked_document = True
                await session.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
            else:
                await session.send("Fetch.continueRequest", {"requestId": request_id})
            return
        try:
            status = event.get("responseStatusCode")
            headers = {
                str(entry.get("name", "")).lower(): str(entry.get("value", ""))
                for entry in event.get("responseHeaders", [])
                if isinstance(entry, dict)
            }
            media_type = parse_media_type(headers.get("content-type"))
            disposition = headers.get("content-disposition", "").lower()
            is_redirect = status in {301, 302, 303, 307, 308}
            should_capture = (
                not is_redirect
                and "attachment" not in disposition
                and media_type not in {"text/html", "application/xhtml+xml"}
                and accepted_media_type(
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


async def _enable_navigation_document_capture(
    page,
    accepted_content_types: tuple[str, ...],
    exclusions: tuple[UrlExclusion, ...] = (),
) -> _NavigationArtifactCapture | None:
    capture_documents = any(
        media_type not in {"text/html", "application/xhtml+xml"}
        for media_type in accepted_content_types
    )
    if not capture_documents and not exclusions:
        return None
    session = await page.context.new_cdp_session(page)
    capture = _NavigationArtifactCapture(accepted_content_types, exclusions)
    if exclusions:
        tree = await session.send("Page.getFrameTree")
        capture.main_frame_id = tree["frameTree"]["frame"]["id"]
    session.on(
        "Fetch.requestPaused",
        lambda event: capture.handle_paused(session, event),
    )
    patterns = [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Response"}] if capture_documents else []
    if exclusions:
        patterns.append({"urlPattern": "*", "requestStage": "Request"})
    await session.send("Fetch.enable", {"patterns": patterns})
    return capture


@asynccontextmanager
async def connect_cdp(playwright: Playwright):
    """Own one bounded standard-CDP connection, before any page authorization."""
    browser = None
    try:
        try:
            async with asyncio.timeout(5):
                browser = await playwright.chromium.connect_over_cdp(get_str("PERIPLUS_CDP_URL"), timeout=5000)
        except (TimeoutError, PlaywrightTimeoutError, OSError) as exc:
            raise CdpUnavailable("CDP connection unavailable.") from exc
        except PlaywrightError as exc:
            _raise_if_playwright_runtime_lost(exc)
            raise CdpUnavailable("CDP connection unavailable.") from exc
        yield browser
    finally:
        if browser is not None:
            try:
                async with asyncio.timeout(5):
                    await browser.close()
            except PlaywrightError as exc:
                _raise_if_playwright_runtime_lost(exc)
            except TimeoutError:
                logging.getLogger(__name__).warning("CDP connection cleanup exceeded its five-second bound")


async def capture_page(
    url: str,
    policy,
    *,
    attempt_number: int,
    browser: Browser,
    timeout_seconds: float = ACQUISITION_TIMEOUT_SECONDS,
    exclusions: tuple[UrlExclusion, ...] = (),
) -> AcquisitionResult:
    if not 1 <= timeout_seconds <= 3600:
        raise ValueError("capture timeout must be between 1 and 3600 seconds")
    started = time.perf_counter()
    started_at = datetime.now(UTC)
    try:
        async with asyncio.timeout(timeout_seconds):
            try:
                completion = policy.content.completion
                # A static policy is expressed by omitting browser-only CDP
                # operations. Explicitly disabling script execution is itself
                # such an operation and would force a lazy HTTP CDP facade to
                # promote the request to a browser.
                page = await browser.new_page()
                document_capture = await _enable_navigation_document_capture(
                    page,
                    policy.content.accepted_content_types,
                    exclusions,
                )
                steps: list[AcquisitionStepEvidence] = []
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
                    navigation_timeout_detail = str(exc) or "Page navigation timed out"
                    if completion.browser_interaction_enabled:
                        meaningful = await with_context_recovery(
                            page,
                            lambda: document_is_meaningful(page),
                            completion.navigation,
                        )
                    elif not page.url or page.url == "about:blank":
                        meaningful = False
                    else:
                        html = await page.content()
                        meaningful = html_is_meaningful(html)
                    if not meaningful:
                        return acquisition_failure(
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
                        await with_context_recovery(
                            page,
                            lambda: stop_navigation(page),
                            completion.navigation,
                        )
                    response = None
                except PlaywrightError as exc:
                    if document_capture is not None and document_capture.blocked_document:
                        await _cancel_event_wait(download_wait)
                        await _cancel_event_wait(response_wait)
                        return acquisition_failure(url, started, started_at, attempt_number,
                            "Document navigation blocked by crawler exclusion policy", "global_exclusion", "navigation", False)
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
                        return acquisition_failure(
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
                    media_type = parse_media_type(content_type)
                    retry_after = parse_retry_after(
                        await response.header_value("retry-after")
                    )
                    rejection = policy_rejection(
                        url=final_url,
                        requested_url=url,
                        started=started,
                        started_at=started_at,
                        attempt_number=attempt_number,
                        status=status,
                        media_type=media_type,
                        retry_after=retry_after,
                        policy=policy,
                    )
                    if rejection is not None:
                        await download.cancel()
                        return rejection
                    try:
                        document_bytes = await _download_bytes(download)
                    except (OSError, PlaywrightError) as download_exc:
                        return acquisition_failure(
                            url,
                            started,
                            started_at,
                            attempt_number,
                            str(download_exc),
                            "download_failed",
                            "acquisition",
                            True,
                        )
                    evidence = attempt_evidence(
                        number=attempt_number,
                        started_at=started_at,
                        requested_url=url,
                        final_url=final_url,
                        status_code=status,
                        media_type=media_type,
                        outcome="success",
                    )
                    return AcquisitionResult(
                        url=final_url,
                        success=True,
                        status_code=status,
                        duration_seconds=time.perf_counter() - started,
                        outcome="success",
                        response_media_type=media_type,
                        document_bytes=document_bytes,
                        attempt_evidence=evidence,
                    )
                else:
                    await _cancel_event_wait(download_wait)
                    await _cancel_event_wait(response_wait)
                status = response.status if response is not None else None
                final_url = page.url
                content_type = (
                    await response.header_value("content-type")
                    if response is not None
                    else None
                )
                media_type = parse_media_type(content_type)
                retry_after = parse_retry_after(
                    await response.header_value("retry-after")
                    if response is not None
                    else None
                )
                rejection = policy_rejection(
                    url=final_url,
                    requested_url=url,
                    started=started,
                    started_at=started_at,
                    attempt_number=attempt_number,
                    status=status,
                    media_type=media_type,
                    retry_after=retry_after,
                    policy=policy,
                )
                if rejection is not None:
                    return rejection
                if media_type not in {"text/html", "application/xhtml+xml"}:
                    if document_capture is None:
                        raise AssertionError(
                            "non-HTML response was accepted without raw capture"
                        )
                    if document_capture.error is not None:
                        return acquisition_failure(
                            url,
                            started,
                            started_at,
                            attempt_number,
                            document_capture.error,
                            "document_capture_failed",
                            "acquisition",
                            True,
                        )
                    if document_capture.body is None:
                        return acquisition_failure(
                            url,
                            started,
                            started_at,
                            attempt_number,
                            "raw navigation response body was not captured",
                            "document_capture_failed",
                            "acquisition",
                            True,
                        )
                    document_bytes = document_capture.body
                    evidence = attempt_evidence(
                        number=attempt_number,
                        started_at=started_at,
                        requested_url=url,
                        final_url=final_url,
                        status_code=status,
                        media_type=media_type,
                        outcome="success",
                    )
                    return AcquisitionResult(
                        url=final_url,
                        success=True,
                        status_code=status,
                        duration_seconds=time.perf_counter() - started,
                        outcome="success",
                        response_media_type=media_type,
                        document_bytes=document_bytes,
                        attempt_evidence=evidence,
                    )
                if completion.wait_dynamic.enabled:
                    steps.append(
                        await with_context_recovery(
                            page,
                            lambda: wait_dynamic(
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
                        await with_context_recovery(
                            page,
                            lambda: wait_fixed(
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
                        await with_context_recovery(
                            page,
                            lambda: scroll(
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
                        await with_context_recovery(
                            page,
                            lambda: expand(
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
                        await with_context_recovery(
                            page,
                            lambda: scroll(
                                page,
                                completion.scroll,
                                attempt_number=attempt_number,
                                step_ordinal=len(steps) + 1,
                            ),
                            completion.navigation,
                        )
                    )
                if html is None:
                    html = await with_context_recovery(
                        page,
                        lambda: page_content(page),
                        completion.navigation,
                    )
                final_url = page.url
                if navigation_timed_out and not html.strip():
                    return acquisition_failure(
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
                    return acquisition_failure(
                        url,
                        started,
                        started_at,
                        attempt_number,
                        str(exc),
                        "execution_context_replaced",
                        "completion",
                        True,
                    )
                if not html_is_meaningful(html):
                    return acquisition_failure(
                        url,
                        started,
                        started_at,
                        attempt_number,
                        str(exc),
                        "execution_context_replaced",
                        "completion",
                        True,
                    )
                final_url = page.url
            except PlaywrightTimeoutError as exc:
                return acquisition_failure(
                    url,
                    started,
                    started_at,
                    attempt_number,
                    str(exc) or "Page navigation timed out",
                    "navigation_timeout",
                    "navigation",
                    True,
                )
            except PlaywrightError as exc:
                _raise_if_playwright_runtime_lost(exc)
                return acquisition_failure(
                    url,
                    started,
                    started_at,
                    attempt_number,
                    str(exc),
                    "navigation_failed",
                    "navigation",
                    True,
                )
        evidence = attempt_evidence(
            number=attempt_number,
            started_at=started_at,
            requested_url=url,
            final_url=final_url,
            status_code=status,
            media_type=media_type,
            outcome="success",
        )
        return AcquisitionResult(
            url=final_url,
            success=True,
            status_code=status,
            duration_seconds=time.perf_counter() - started,
            html=html,
            outcome="success",
            response_media_type=media_type,
            attempt_evidence=evidence,
            steps=tuple(steps),
        )
    except TimeoutError as exc:
        return acquisition_failure(
            url,
            started,
            started_at,
            attempt_number,
            str(exc) or "Page acquisition timed out",
            "acquisition_timeout",
            "acquisition",
            True,
        )
    except PlaywrightError as exc:
        _raise_if_playwright_runtime_lost(exc)
        raise
