"""Direct Prometheus metrics for repository ingestion."""

from __future__ import annotations

import threading
import time

from prometheus_client import Counter, Gauge, Histogram

_raw_writes = Counter("atlas_repository_raw_writes_total", "Raw content write outcomes.", ("outcome",))
_attempts = Counter("atlas_repository_ingestion_attempts_total", "Repository ingestion outcomes.", ("outcome",))
_batches = Counter("atlas_repository_ingestion_batches_total", "Repository batch outcomes.", ("outcome",))
_duration = Histogram("atlas_repository_ingestion_duration_seconds", "Repository ingestion phase duration.", ("phase", "outcome"))
_batch_items = Histogram("atlas_repository_ingestion_batch_items", "Items per repository batch.")
_raw_bytes = Histogram(
    "atlas_repository_raw_write_bytes",
    "Raw logical and stored bytes per repository write.",
    ("representation",),
)
_batch_element_rows = Histogram(
    "atlas_repository_ingestion_batch_element_rows",
    "DOM element rows per repository batch.",
)
_batch_staged_bytes = Histogram(
    "atlas_repository_ingestion_batch_staged_bytes",
    "Arrow or Parquet bytes staged per repository batch.",
)
_pending = Gauge("atlas_repository_ingestion_jobs_pending", "Repository jobs waiting in JetStream.")
_ack_pending = Gauge("atlas_repository_ingestion_jobs_ack_pending", "Delivered repository jobs awaiting acknowledgement.")
_redelivered = Gauge("atlas_repository_ingestion_jobs_redelivered", "Redelivered repository jobs.")
_queue_stalled = Gauge(
    "atlas_repository_ingestion_queue_stalled",
    "Whether the ingestion queue has exceeded its no-progress threshold.",
)
_oldest_pending_age = Gauge(
    "atlas_repository_ingestion_oldest_pending_age_seconds",
    "Lower-bound age of an ingestion queue that has not made progress.",
)
_lane_operation_duration = Gauge(
    "atlas_repository_ingestion_lane_operation_duration_seconds",
    "Elapsed duration of the current ingestion-lane operation.",
    ("lane",),
)
_lane_operation = Gauge(
    "atlas_repository_ingestion_lane_operation",
    "Current ingestion-lane operation as a one-hot state.",
    ("lane", "operation"),
)
_lane_last_commit = Gauge(
    "atlas_repository_ingestion_lane_last_successful_commit_timestamp_seconds",
    "Unix timestamp of the lane's last successful DuckLake commit.",
    ("lane",),
)
_lane_client_generation = Gauge(
    "atlas_repository_ingestion_lane_client_generation",
    "Current session-affine DuckBasin client generation.",
    ("lane",),
)
_lane_client_remints = Gauge(
    "atlas_repository_ingestion_lane_client_remints",
    "Successful DuckBasin client remints observed by the lane.",
    ("lane",),
)
_lane_token_generation = Gauge(
    "atlas_repository_ingestion_lane_token_generation",
    "Current DuckBasin service-account token generation.",
    ("lane",),
)
_lane_token_status = Gauge(
    "atlas_repository_ingestion_lane_token_status",
    "DuckBasin token refresh status as a one-hot state.",
    ("lane", "status"),
)
_lane_circuit_state = Gauge(
    "atlas_repository_ingestion_lane_circuit_state",
    "DuckBasin readiness circuit state as a one-hot state.",
    ("lane", "state"),
)
_lane_recoveries = Counter(
    "atlas_repository_ingestion_lane_recoveries_total",
    "Ingestion-lane recovery and restart outcomes.",
    ("lane", "reason"),
)

_TOKEN_STATES = (
    "unknown",
    "empty",
    "valid",
    "refreshing",
    "expired",
    "backoff",
    "failed",
)
_CIRCUIT_STATES = ("closed", "open", "half_open", "recovering")


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


def batch(*, outcome: str, duration_seconds: float, items: int, element_rows: int, staged_bytes: int) -> None:
    _batches.labels(outcome).inc()
    _duration.labels("commit", outcome).observe(max(0.0, duration_seconds))
    _batch_items.observe(items)
    _batch_element_rows.observe(max(0, element_rows))
    _batch_staged_bytes.observe(max(0, staged_bytes))


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
        self._token_status = "unknown"
        self._circuit_state = "half_open"
        _lane_operation_duration.labels(self._lane).set_function(
            self.current_operation_duration
        )
        self.operation_started("idle")
        self.token(status="unknown", generation=0)
        self.circuit("half_open")
        _lane_last_commit.labels(self._lane).set(0)
        _lane_client_generation.labels(self._lane).set(1)
        _lane_client_remints.labels(self._lane).set(0)

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

    def client(self, *, generation: int, remints: int) -> None:
        _lane_client_generation.labels(self._lane).set(max(1, generation))
        _lane_client_remints.labels(self._lane).set(max(0, remints))

    def token(self, *, status: str, generation: int) -> None:
        normalized = status if status in _TOKEN_STATES else "unknown"
        with self._lock:
            previous = self._token_status
            self._token_status = normalized
        _lane_token_status.labels(self._lane, previous).set(0)
        _lane_token_status.labels(self._lane, normalized).set(1)
        _lane_token_generation.labels(self._lane).set(max(0, generation))

    def circuit(self, state: str) -> None:
        normalized = state if state in _CIRCUIT_STATES else "open"
        with self._lock:
            previous = self._circuit_state
            self._circuit_state = normalized
        _lane_circuit_state.labels(self._lane, previous).set(0)
        _lane_circuit_state.labels(self._lane, normalized).set(1)

    def recovery(self, reason: str) -> None:
        _lane_recoveries.labels(self._lane, reason).inc()
