"""Prometheus telemetry for visit-scoped rebuild batches."""

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

from prometheus_client import Counter, Gauge, Histogram
from periplus.platform.telemetry import DURATION_BUCKETS

_batches = Counter(
    "periplus_materialization_batches_total",
    "Materialization batch outcomes.",
    ("outcome",),
)
_source_items = Counter(
    "periplus_materialization_source_items_total",
    "Source visits processed by materialization.",
)
_source_bytes = Counter(
    "periplus_materialization_source_bytes_total",
    "Logical source document bytes processed by materialization.",
)
_output_rows = Counter(
    "periplus_materialization_output_rows_total",
    "Rows committed by materialization.",
)
_output_bytes = Counter(
    "periplus_materialization_output_bytes_total",
    "Parquet and local projection bytes committed by materialization.",
)
_phase = Histogram(
    "periplus_materialization_phase_duration_seconds",
    "Materialization batch phase duration.",
    ("phase",),
    buckets=DURATION_BUCKETS,
)
_conflicts = Counter(
    "periplus_materialization_conflicts_total",
    "DuckLake transaction conflicts observed by materialization.",
)
_retries = Counter(
    "periplus_materialization_retries_total",
    "Materialization batch retries.",
    ("reason",),
)
_failures = Counter(
    "periplus_materialization_failures_total",
    "Terminal materialization failures.",
    ("phase",),
)
_queue = Gauge(
    "periplus_materialization_queue_messages",
    "JetStream materialization batch consumer state.",
    ("state",),
)
_progress = Gauge(
    "periplus_materialization_last_observed_rebuild_progress_ratio",
    "Last locally observed rebuild progress; not authoritative fleet state.",
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
    superseded: bool = False,
) -> None:
    _batches.labels("superseded" if superseded else "redelivered" if already_applied else "committed").inc()
    if not already_applied and not superseded:
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
    _queue_observed.set_to_current_time()
    _queue.labels("pending").set(max(0, pending))
    _queue.labels("ack_pending").set(max(0, ack_pending))
    _queue.labels("redelivered").set(max(0, redelivered))


_progress_observed = Gauge("periplus_materialization_progress_observed_timestamp_seconds", "Timestamp of last local rebuild progress observation.")
_queue_observed = Gauge("periplus_materialization_queue_observed_timestamp_seconds", "Timestamp of last successful shared queue observation.")

def progress(*, completed: int, total: int) -> None:
    _progress_observed.set_to_current_time()
    _progress.set(0 if total <= 0 else min(1, max(0, completed / total)))


_step = Histogram(
    "periplus_materialization_step_duration_seconds",
    "Materialization operation wall time, including failed attempts; not CPU time.",
    ("step", "outcome"),
    buckets=DURATION_BUCKETS,
)

_preparations = Counter(
    "periplus_materialization_preparation_attempts_total",
    "Batch projection preparations, including preparations later discarded.",
)


def preparation_attempt() -> None:
    _preparations.inc()


@contextmanager
def step(name: str) -> Iterator[None]:
    """Observe completed operation attempts, including exceptions and retries.

    Callers use fixed operation names, never document or batch identities.
    """
    started = perf_counter()
    outcome = "error"
    try:
        yield
        outcome = "success"
    finally:
        _step.labels(name, outcome).observe(max(0.0, perf_counter() - started))
