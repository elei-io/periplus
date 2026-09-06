"""Direct Prometheus metrics for repository ingestion."""

from __future__ import annotations

import threading
import time

from prometheus_client import Counter, Gauge, Histogram

_raw_writes = Counter("periplus_repository_raw_writes_total", "Raw content write outcomes.", ("outcome",))
_attempts = Counter("periplus_repository_ingestion_attempts_total", "Repository ingestion outcomes.", ("outcome",))
_batches = Counter("periplus_repository_ingestion_batches_total", "Repository batch outcomes.", ("outcome",))
_duration = Histogram("periplus_repository_ingestion_duration_seconds", "Repository ingestion phase duration.", ("phase", "outcome"))
_batch_items = Histogram("periplus_repository_ingestion_batch_items", "Items per repository batch.")
_raw_bytes = Histogram(
    "periplus_repository_raw_write_bytes",
    "Raw logical and stored bytes per repository write.",
    ("representation",),
)
_pending = Gauge("periplus_repository_ingestion_jobs_pending", "Repository jobs waiting in JetStream.")
_ack_pending = Gauge("periplus_repository_ingestion_jobs_ack_pending", "Delivered repository jobs awaiting acknowledgement.")
_redelivered = Gauge("periplus_repository_ingestion_jobs_redelivered", "Redelivered repository jobs.")
_queue_stalled = Gauge(
    "periplus_repository_ingestion_queue_stalled",
    "Whether the ingestion queue has exceeded its no-progress threshold.",
)
_oldest_pending_age = Gauge(
    "periplus_repository_ingestion_oldest_pending_age_seconds",
    "Lower-bound age of an ingestion queue that has not made progress.",
)
_lane_operation_duration = Gauge(
    "periplus_repository_ingestion_lane_operation_duration_seconds",
    "Elapsed duration of the current ingestion-lane operation.",
    ("lane",),
)
_lane_operation = Gauge(
    "periplus_repository_ingestion_lane_operation",
    "Current ingestion-lane operation as a one-hot state.",
    ("lane", "operation"),
)
_lane_last_commit = Gauge(
    "periplus_repository_ingestion_lane_last_successful_commit_timestamp_seconds",
    "Unix timestamp of the lane's last successful DuckLake commit.",
    ("lane",),
)
_lane_recoveries = Counter(
    "periplus_repository_ingestion_lane_recoveries_total",
    "Ingestion-lane recovery and restart outcomes.",
    ("lane", "reason"),
)

def raw_write(*, outcome: str, duration_seconds: float, html_bytes: int | None = None, compressed_bytes: int | None = None) -> None:
    _raw_writes.labels(outcome).inc()
    _duration.labels("raw_write", outcome).observe(max(0.0, duration_seconds))
    if html_bytes is not None:
        _raw_bytes.labels("logical").observe(max(0, html_bytes))
    if compressed_bytes is not None:
        _raw_bytes.labels("stored").observe(max(0, compressed_bytes))


def attempt(*, outcome: str, queue_seconds: float) -> None:
    _attempts.labels(outcome).inc()
    _duration.labels("queue", outcome).observe(max(0.0, queue_seconds))


def preparation(*, outcome: str, duration_seconds: float) -> None:
    _duration.labels("preparation", outcome).observe(max(0.0, duration_seconds))


def batch(*, outcome: str, duration_seconds: float, items: int) -> None:
    _batches.labels(outcome).inc()
    _duration.labels("commit", outcome).observe(max(0.0, duration_seconds))
    _batch_items.observe(items)


def queue_state(
    *,
    pending: int,
    ack_pending: int,
    redelivered: int,
    oldest_pending_age_seconds: float = 0.0,
    stalled: bool = False,
) -> None:
    _pending.set(pending)
    _ack_pending.set(ack_pending)
    _redelivered.set(redelivered)
    _oldest_pending_age.set(max(0.0, oldest_pending_age_seconds))
    _queue_stalled.set(1 if stalled else 0)


class IngestionLaneMetrics:
    """Mutable per-lane telemetry without coupling metrics to worker health."""

    def __init__(self, lane_index: int) -> None:
        self._lane = str(lane_index)
        self._lock = threading.Lock()
        self._operation = "idle"
        self._operation_started = time.monotonic()
        _lane_operation_duration.labels(self._lane).set_function(
            self.current_operation_duration
        )
        self.operation_started("idle")
        _lane_last_commit.labels(self._lane).set(0)

    def current_operation_duration(self) -> float:
        with self._lock:
            return max(0.0, time.monotonic() - self._operation_started)

    def operation_started(self, operation: str) -> None:
        with self._lock:
            previous = self._operation
            self._operation = operation
            self._operation_started = time.monotonic()
        _lane_operation.labels(self._lane, previous).set(0)
        _lane_operation.labels(self._lane, operation).set(1)

    def operation_finished(self) -> None:
        self.operation_started("idle")

    def commit_succeeded(self) -> None:
        _lane_last_commit.labels(self._lane).set_to_current_time()

    def recovery(self, reason: str) -> None:
        _lane_recoveries.labels(self._lane, reason).inc()
