"""Prometheus metrics for scoped catalogue materialization work."""

from prometheus_client import Gauge, Histogram

_duration = Histogram(
    "atlas_materialization_operation_duration_seconds",
    "Scoped materialization phase duration.",
    ("phase", "outcome"),
)
_pending = Gauge(
    "atlas_materialization_jobs_pending",
    "Materialization jobs waiting in JetStream.",
    ("phase",),
)
_ack_pending = Gauge(
    "atlas_materialization_jobs_ack_pending",
    "Delivered materialization jobs awaiting acknowledgement.",
    ("phase",),
)


def operation(*, phase: str, outcome: str, duration_seconds: float) -> None:
    _duration.labels(phase, outcome).observe(max(0.0, duration_seconds))


def queue_state(*, phase: str, pending: int, ack_pending: int) -> None:
    _pending.labels(phase).set(pending)
    _ack_pending.labels(phase).set(ack_pending)
