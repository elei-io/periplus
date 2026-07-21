"""Prometheus metrics for the DuckLake CDC to JetStream publication boundary."""

from __future__ import annotations

from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram


_tables = Gauge(
    "atlas_catalogue_relay_tables",
    "Live DuckLake tables currently indexed for NATS publication.",
)
_source_snapshot = Gauge(
    "atlas_catalogue_relay_source_snapshot",
    "Latest DuckLake table snapshot observed by the relay.",
    ("table_uuid", "schema", "table"),
)
_published_snapshot = Gauge(
    "atlas_catalogue_relay_published_snapshot",
    "Latest DuckLake table snapshot durably published to JetStream.",
    ("table_uuid", "schema", "table"),
)
_tick_lag = Gauge(
    "atlas_catalogue_relay_tick_lag_seconds",
    "Age of the latest DuckLake table tick when it reached JetStream.",
    ("table_uuid", "schema", "table"),
)
_ddl_snapshot = Gauge(
    "atlas_catalogue_relay_ddl_snapshot",
    "Latest DuckLake DDL snapshot durably published to JetStream.",
)
_ddl_lag = Gauge(
    "atlas_catalogue_relay_ddl_lag_seconds",
    "Age of the latest DuckLake DDL change when it reached JetStream.",
)
_publications = Counter(
    "atlas_catalogue_relay_publications_total",
    "Catalogue relay publication attempts.",
    ("kind", "outcome"),
)
_publication_duration = Histogram(
    "atlas_catalogue_relay_publication_duration_seconds",
    "Time spent durably publishing one catalogue event to JetStream.",
    ("kind", "outcome"),
)
_failures = Counter(
    "atlas_catalogue_relay_failures_total",
    "Catalogue relay loop failures.",
    ("phase",),
)


def table_count(value: int) -> None:
    _tables.set(max(0, value))


def track_table(*, table_uuid: str, schema_name: str, table_name: str) -> None:
    labels = (table_uuid, schema_name, table_name)
    for metric in (_source_snapshot, _published_snapshot, _tick_lag):
        metric.labels(*labels)


def source_tick(
    *,
    table_uuid: str,
    schema_name: str,
    table_name: str,
    snapshot_id: int,
) -> None:
    _source_snapshot.labels(table_uuid, schema_name, table_name).set(snapshot_id)


def published_tick(
    *,
    table_uuid: str,
    schema_name: str,
    table_name: str,
    snapshot_id: int,
    snapshot_time: datetime | None,
) -> None:
    labels = (table_uuid, schema_name, table_name)
    _published_snapshot.labels(*labels).set(snapshot_id)
    if snapshot_time is not None:
        _tick_lag.labels(*labels).set(_age_seconds(snapshot_time))


def remove_table(*, table_uuid: str, schema_name: str, table_name: str) -> None:
    labels = (table_uuid, schema_name, table_name)
    for metric in (_source_snapshot, _published_snapshot, _tick_lag):
        metric.remove(*labels)


def published_ddl(*, snapshot_id: int, snapshot_time: datetime | None) -> None:
    _ddl_snapshot.set(snapshot_id)
    if snapshot_time is not None:
        _ddl_lag.set(_age_seconds(snapshot_time))


def publication(*, kind: str, outcome: str, duration_seconds: float) -> None:
    _publications.labels(kind, outcome).inc()
    _publication_duration.labels(kind, outcome).observe(
        max(0.0, duration_seconds)
    )


def failure(*, phase: str) -> None:
    _failures.labels(phase).inc()


def _age_seconds(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - value).total_seconds())
