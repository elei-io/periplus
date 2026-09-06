"""Prometheus metrics for acquisition-owned graph navigation packages."""

from prometheus_client import Counter, Histogram

_packages = Counter(
    "periplus_navigation_packages_total",
    "Navigation package outcomes.",
    ("phase", "outcome"),
)
_duration = Histogram(
    "periplus_navigation_package_duration_seconds",
    "Navigation package generation and write duration.",
    ("phase", "outcome"),
)
_rows = Histogram(
    "periplus_navigation_package_rows",
    "Link rows in generated navigation packages.",
)
_bytes = Histogram(
    "periplus_navigation_package_bytes",
    "Bytes in generated navigation packages.",
)


def package(
    *,
    phase: str,
    outcome: str,
    duration_seconds: float,
    rows: int | None = None,
    byte_count: int | None = None,
) -> None:
    _packages.labels(phase, outcome).inc()
    _duration.labels(phase, outcome).observe(max(0.0, duration_seconds))
    if rows is not None:
        _rows.observe(rows)
    if byte_count is not None:
        _bytes.observe(byte_count)
