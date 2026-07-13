"""Direct Prometheus metrics for page acquisition."""

from prometheus_client import Counter, Histogram

from actions.crawl.schemas import CrawlPage
_acquisitions = Counter("atlas_page_acquisitions_total", "Page acquisition outcomes.", ("outcome", "profile", "source", "domain_group"))
_duration = Histogram("atlas_page_acquisition_duration_seconds", "Page acquisition duration.", ("domain_group", "profile", "outcome", "source"))
_failures = Counter("atlas_crawl_failures_total", "Crawl failures.", ("reason", "domain_group", "profile"))
_persisted = Counter("atlas_crawls_persisted_total", "Durable crawls created.", ("outcome", "profile", "domain_group"))
_cache = Counter("atlas_repository_cache_lookups_total", "Repository cache outcomes.", ("outcome",))
_cache_age = Histogram("atlas_repository_cache_entry_age_seconds", "Age of reused repository entries.", ("outcome",))


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


def page_acquisition(*, page: CrawlPage, duration_seconds: float, mode: str, source: str, domain_group: str, outcome: str | None = None) -> None:
    outcome = outcome or ("succeeded" if page.success else "failed")
    _acquisitions.labels(outcome, mode, source, domain_group).inc()
    _duration.labels(domain_group, mode, outcome, source).observe(max(0.0, duration_seconds))
    if outcome == "failed":
        _failures.labels(failure_reason(page), domain_group, mode).inc()


def crawl_failure(*, page: CrawlPage, mode: str, domain_group: str) -> None:
    _failures.labels(failure_reason(page), domain_group, mode).inc()


def crawl_persisted(*, page: CrawlPage, mode: str, domain_group: str) -> None:
    _persisted.labels("succeeded" if page.success else "failed", mode, domain_group).inc()


def repository_cache(*, outcome: str, age_seconds: float | None = None) -> None:
    _cache.labels(outcome).inc()
    if age_seconds is not None:
        _cache_age.labels(outcome).observe(max(0.0, age_seconds))
