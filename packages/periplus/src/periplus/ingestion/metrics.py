"""Raw payload write metrics; corpus progress comes from archive checkpoints."""

from prometheus_client import Counter, Histogram
from periplus.platform.telemetry import DURATION_BUCKETS, BYTE_BUCKETS

_raw_writes = Counter(
    "periplus_repository_raw_writes_total", "Raw content write outcomes.", ("outcome",)
)
_duration = Histogram(
    "periplus_repository_ingestion_duration_seconds",
    "Raw write duration.",
    ("phase", "outcome"),
    buckets=DURATION_BUCKETS,
)
_raw_bytes = Histogram(
    "periplus_repository_raw_write_bytes",
    "Logical and stored payload bytes.",
    ("representation",),
    buckets=BYTE_BUCKETS,
)


def raw_write(
    *,
    outcome: str,
    duration_seconds: float,
    html_bytes: int | None = None,
    compressed_bytes: int | None = None,
) -> None:
    _raw_writes.labels(outcome).inc()
    _duration.labels("raw_write", outcome).observe(max(0.0, duration_seconds))
    if html_bytes is not None:
        _raw_bytes.labels("logical").observe(max(0, html_bytes))
    if compressed_bytes is not None:
        _raw_bytes.labels("stored").observe(max(0, compressed_bytes))
