"""Prometheus telemetry for visit-scoped rebuild batches."""

from prometheus_client import Counter, Gauge, Histogram

_batches = Counter(
    "atlas_materialization_batches_total",
    "Materialization batch outcomes.",
    ("outcome",),
)
_source_items = Counter(
    "atlas_materialization_source_items_total",
    "Source visits processed by materialization.",
)
_source_bytes = Counter(
    "atlas_materialization_source_bytes_total",
    "Logical source document bytes processed by materialization.",
)
_output_rows = Counter(
    "atlas_materialization_output_rows_total",
    "Rows committed by materialization.",
)
_output_bytes = Counter(
    "atlas_materialization_output_bytes_total",
    "Parquet and local projection bytes committed by materialization.",
)
_phase = Histogram(
    "atlas_materialization_phase_duration_seconds",
    "Materialization batch phase duration.",
    ("phase",),
)
_conflicts = Counter(
    "atlas_materialization_conflicts_total",
    "DuckLake transaction conflicts observed by materialization.",
)
_retries = Counter(
    "atlas_materialization_retries_total",
    "Materialization batch retries.",
    ("reason",),
)
_failures = Counter(
    "atlas_materialization_failures_total",
    "Terminal materialization failures.",
    ("phase",),
)
_queue = Gauge(
    "atlas_materialization_queue_messages",
    "JetStream materialization batch consumer state.",
    ("state",),
)
_progress = Gauge(
    "atlas_materialization_rebuild_progress_ratio",
    "Progress of the most recently observed active rebuild.",
)


def batch(
    *,
    source_items: int,
    source_bytes: int,
    output_rows: int,
    output_bytes: int,
    project_seconds: float,
    parquet_seconds: float,
    commit_seconds: float,
    already_applied: bool,
) -> None:
    _batches.labels("redelivered" if already_applied else "committed").inc()
    _source_items.inc(max(0, source_items))
    _source_bytes.inc(max(0, source_bytes))
    _output_rows.inc(max(0, output_rows))
    _output_bytes.inc(max(0, output_bytes))
    for name, value in (
        ("project", project_seconds),
        ("parquet", parquet_seconds),
        ("commit", commit_seconds),
    ):
        _phase.labels(name).observe(max(0.0, value))


def retry(reason: str) -> None:
    _retries.labels(reason).inc()


def conflict() -> None:
    _conflicts.inc()


def failure(phase: str) -> None:
    _failures.labels(phase).inc()


def queue(*, pending: int, ack_pending: int, redelivered: int) -> None:
    _queue.labels("pending").set(max(0, pending))
    _queue.labels("ack_pending").set(max(0, ack_pending))
    _queue.labels("redelivered").set(max(0, redelivered))


def progress(*, completed: int, total: int) -> None:
    _progress.set(0 if total <= 0 else min(1, max(0, completed / total)))
