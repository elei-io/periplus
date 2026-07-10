from __future__ import annotations

from actions.crawl.schemas import CrawlPage
from actions.shared.crawl import CrawlMode

from .recorder import record


def http_status_class(status_code: int | None) -> str:
    if status_code is None or not 100 <= status_code <= 599:
        return "none"
    return f"{status_code // 100}xx"


def failure_reason(page: CrawlPage) -> str:
    if page.status_code == 429:
        return "http_429"
    if page.status_code is not None and 400 <= page.status_code <= 499:
        return "http_4xx"
    if page.status_code is not None and 500 <= page.status_code <= 599:
        return "http_5xx"
    error = (page.error or "").lower()
    mappings = (
        (("dns", "name resolution", "nodename nor servname"), "dns"),
        (("connect timeout", "connection timed out"), "connect_timeout"),
        (("read timeout",), "read_timeout"),
        (("ssl", "tls", "certificate"), "tls"),
        (("browser", "target closed", "page crashed"), "browser_crash"),
        (("blocked", "captcha", "access denied"), "content_blocked"),
        (("capacity", "permit"), "capacity_timeout"),
        (("lease",), "capacity_lease_lost"),
    )
    for needles, reason in mappings:
        if any(needle in error for needle in needles):
            return reason
    return "navigation" if error else "unknown"


def navigation(*, page: CrawlPage, mode: CrawlMode, domain_group: str) -> None:
    outcome = "succeeded" if page.success else "failed"
    record(
        "atlas_crawl_navigation_duration_seconds",
        page.duration_seconds,
        domain_group=domain_group,
        mode=mode,
        outcome=outcome,
    )


def page_acquisition(
    *,
    page: CrawlPage,
    duration_seconds: float,
    mode: CrawlMode,
    source: str,
    domain_group: str,
    outcome: str | None = None,
) -> None:
    outcome = outcome or ("succeeded" if page.success else "failed")
    record(
        "atlas_page_acquisitions_total",
        outcome=outcome,
        mode=mode,
        source=source,
        domain_group=domain_group,
    )
    record(
        "atlas_page_acquisition_duration_seconds",
        duration_seconds,
        domain_group=domain_group,
        mode=mode,
        outcome=outcome,
        source=source,
    )
    record(
        "atlas_crawl_http_responses_total",
        status_class=http_status_class(page.status_code),
        domain_group=domain_group,
    )
    if page.html is not None:
        record(
            "atlas_crawl_response_size_bytes",
            len(page.html.encode("utf-8")),
            domain_group=domain_group,
        )
    if page.crawl and page.crawl.get("redirected_url"):
        record("atlas_crawl_redirects_total", domain_group=domain_group)
    if outcome == "failed":
        record(
            "atlas_crawl_failures_total",
            reason=failure_reason(page),
            domain_group=domain_group,
            mode=mode,
        )
    for warning in page.artifact_warnings:
        record(
            "atlas_crawl_warnings_total",
            kind=str(warning.code),
            domain_group=domain_group,
        )


def crawl_failure(*, page: CrawlPage, mode: CrawlMode, domain_group: str) -> None:
    record(
        "atlas_crawl_failures_total",
        reason=failure_reason(page),
        domain_group=domain_group,
        mode=mode,
    )


def crawl_persisted(*, page: CrawlPage, mode: CrawlMode, domain_group: str) -> None:
    record(
        "atlas_crawls_persisted_total",
        outcome="succeeded" if page.success else "failed",
        mode=mode,
        domain_group=domain_group,
    )
