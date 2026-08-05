"""Prometheus metrics for the Periplus acquisition queue."""

from prometheus_client import Gauge


_queue_pending = Gauge(
    "periplus_acquisition_jobs_pending",
    "Acquisition requests queued or actively claimed.",
)
_queue_oldest_age = Gauge(
    "periplus_acquisition_oldest_pending_age_seconds",
    "Time since this process first observed the current nonempty acquisition queue.",
)


def queue_state(*, pending: int, oldest_age_seconds: float) -> None:
    _queue_pending.set(max(0, pending))
    _queue_oldest_age.set(max(0.0, oldest_age_seconds))
