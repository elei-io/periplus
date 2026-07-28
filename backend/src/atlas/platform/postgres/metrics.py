"""Connection ownership metrics for the bounded Postgres pool."""

from __future__ import annotations

from time import monotonic

from prometheus_client import Gauge, Histogram
from sqlalchemy import event
from sqlalchemy.engine import Engine


_checked_out = Gauge(
    "atlas_postgres_connections_checked_out",
    "Postgres connections currently owned by this Atlas process.",
)
_hold_seconds = Histogram(
    "atlas_postgres_connection_hold_seconds",
    "Time a Postgres connection remains checked out.",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)


def instrument_postgres_pool(engine: Engine) -> None:
    @event.listens_for(engine, "checkout")
    def checked_out(_connection, record, _proxy) -> None:
        record.info["atlas_checkout_started_at"] = monotonic()
        _checked_out.inc()

    @event.listens_for(engine, "checkin")
    def checked_in(_connection, record) -> None:
        started_at = record.info.pop(
            "atlas_checkout_started_at", None
        )
        if started_at is not None:
            _hold_seconds.observe(max(0, monotonic() - started_at))
            _checked_out.dec()
