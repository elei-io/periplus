"""Direct Prometheus metrics for page acquisition."""

from prometheus_client import Counter, Gauge, Histogram

from actions.crawl.schemas import CrawlPage
_acquisitions = Counter("atlas_page_acquisitions_total", "Page acquisition outcomes.", ("outcome", "profile", "source", "remote_domain"))
_duration = Histogram("atlas_page_acquisition_duration_seconds", "Page acquisition duration.", ("remote_domain", "profile", "outcome", "source"))
_failures = Counter("atlas_crawl_failures_total", "Crawl failures.", ("reason", "remote_domain", "profile"))
_persisted = Counter("atlas_crawls_persisted_total", "Durable crawls created.", ("outcome", "profile", "remote_domain"))
_cache = Counter("atlas_repository_cache_lookups_total", "Repository cache outcomes.", ("outcome",))
_cache_age = Histogram("atlas_repository_cache_entry_age_seconds", "Age of reused repository entries.", ("outcome",))
_queue_pending = Gauge(
    "atlas_acquisition_jobs_pending",
    "Acquisition requests queued or actively claimed.",
    ("transport",),
)
_queue_oldest_age = Gauge(
    "atlas_acquisition_oldest_pending_age_seconds",
    "Age of the oldest acquisition request.",
    ("transport",),
)


def queue_state(*, transport: str, pending: int, oldest_age_seconds: float) -> None:
    _queue_pending.labels(transport).set(max(0, pending))
    _queue_oldest_age.labels(transport).set(max(0.0, oldest_age_seconds))


def failure_reason(page: CrawlPage) -> str:
    if page.status_code == 429:
        return "http_429"
    if page.status_code is not None and 400 <= page.status_code <= 499:
        return "http_4xx"
    if page.status_code is not None and 500 <= page.status_code <= 599:
        return "http_5xx"
    error = (page.error or "").lower()
    for needles, reason in (
        (("dns", "name resolution"), "dns"),
        (("timeout",), "timeout"),
        (("ssl", "tls", "certificate"), "tls"),
        (("browser", "target closed", "page crashed"), "browser"),
        (("blocked", "captcha", "access denied"), "blocked"),
        (("capacity",), "capacity"),
    ):
        if any(value in error for value in needles):
            return reason
    return "navigation" if error else "unknown"


def page_acquisition(*, page: CrawlPage, duration_seconds: float, mode: str, source: str, remote_domain: str, outcome: str | None = None) -> None:
    outcome = outcome or ("succeeded" if page.success else "failed")
    _acquisitions.labels(outcome, mode, source, remote_domain).inc()
    _duration.labels(remote_domain, mode, outcome, source).observe(max(0.0, duration_seconds))
    if outcome == "failed":
        _failures.labels(failure_reason(page), remote_domain, mode).inc()


def crawl_failure(*, page: CrawlPage, mode: str, remote_domain: str) -> None:
    _failures.labels(failure_reason(page), remote_domain, mode).inc()


def crawl_persisted(*, page: CrawlPage, mode: str, remote_domain: str) -> None:
    _persisted.labels("succeeded" if page.success else "failed", mode, remote_domain).inc()


def repository_cache(*, outcome: str, age_seconds: float | None = None) -> None:
    _cache.labels(outcome).inc()
    if age_seconds is not None:
        _cache_age.labels(outcome).observe(max(0.0, age_seconds))
