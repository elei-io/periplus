"""Browser readiness operations and their durable step evidence."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from html.parser import HTMLParser

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from acquisition.errors import ExecutionContextReplacedError
from acquisition.models import AcquisitionStepEvidence


def _is_execution_context_replaced(exc: PlaywrightError) -> bool:
    detail = str(exc).lower()
    return (
        "execution context was destroyed" in detail
        or "cannot find context with specified id" in detail
    )


async def with_context_recovery[T](
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
                await page.wait_for_timeout(navigation.context_replacement_settle_ms)
    raise AssertionError("unreachable")


async def document_is_meaningful(page) -> bool:
    if not page.url or page.url == "about:blank":
        return False
    metrics = await _page_metrics(page)
    return metrics["elements"] > 3 and metrics["text_chars"] > 0


async def stop_navigation(page) -> None:
    await page.evaluate("window.stop()")


async def page_content(page) -> str:
    return await page.content()


class _MeaningfulHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements = 0
        self.text_chars = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag, attrs
        self.elements += 1

    def handle_data(self, data: str) -> None:
        self.text_chars += len(data.strip())


def html_is_meaningful(html: str) -> bool:
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
) -> AcquisitionStepEvidence:
    config_json = config.model_dump(mode="json")
    encoded = json.dumps(config_json, separators=(",", ":"), sort_keys=True)
    return AcquisitionStepEvidence(
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


async def wait_dynamic(
    page, config, *, attempt_number: int, step_ordinal: int
) -> AcquisitionStepEvidence:
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


async def wait_fixed(
    page, config, *, attempt_number: int, step_ordinal: int
) -> AcquisitionStepEvidence:
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


async def scroll(
    page, config, *, attempt_number: int, step_ordinal: int
) -> AcquisitionStepEvidence:
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


async def expand(
    page, config, *, attempt_number: int, step_ordinal: int
) -> AcquisitionStepEvidence:
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
            "budget_exhausted" if iterations == config.maximum_actions else "expanded"
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
