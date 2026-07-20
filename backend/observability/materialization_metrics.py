"""Prometheus metrics for scoped catalogue materialization work."""

from collections.abc import Iterator
from contextlib import contextmanager
import time

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
_oldest_pending_age = Gauge(
    "atlas_materialization_oldest_pending_age_seconds",
    "Lower-bound age of a materialization queue that has not made progress.",
    ("phase",),
)


def operation(*, phase: str, outcome: str, duration_seconds: float) -> None:
    _duration.labels(phase, outcome).observe(max(0.0, duration_seconds))


@contextmanager
def operation_timer(phase: str) -> Iterator[None]:
    """Observe one synchronous materialization subphase."""

    started = time.perf_counter()
    outcome = "failed"
    try:
        yield
        outcome = "succeeded"
    finally:
        operation(
            phase=phase,
            outcome=outcome,
            duration_seconds=time.perf_counter() - started,
        )


def queue_state(
    *,
    phase: str,
    pending: int,
    ack_pending: int,
    oldest_pending_age_seconds: float = 0.0,
) -> None:
    _pending.labels(phase).set(pending)
    _ack_pending.labels(phase).set(ack_pending)
    _oldest_pending_age.labels(phase).set(max(0.0, oldest_pending_age_seconds))
