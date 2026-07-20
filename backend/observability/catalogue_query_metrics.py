"""Low-cardinality metrics for API-owned interactive catalogue queries."""

from prometheus_client import Counter, Histogram


_queries = Counter(
    "atlas_catalogue_queries_total",
    "Interactive catalogue query outcomes.",
    ("statement_kind", "outcome"),
)
_duration = Histogram(
    "atlas_catalogue_query_duration_seconds",
    "Interactive catalogue query duration.",
    ("statement_kind", "outcome"),
)
_rows = Histogram(
    "atlas_catalogue_query_rows",
    "Rows streamed by an interactive catalogue query.",
    ("statement_kind",),
)
_bytes = Histogram(
    "atlas_catalogue_query_result_bytes",
    "Arrow IPC bytes streamed by an interactive catalogue query.",
    ("statement_kind",),
)


def completed(
    *,
    statement_kind: str,
    outcome: str,
    duration_seconds: float,
    rows: int,
    result_bytes: int,
) -> None:
    _queries.labels(statement_kind, outcome).inc()
    _duration.labels(statement_kind, outcome).observe(max(0.0, duration_seconds))
    _rows.labels(statement_kind).observe(max(0, rows))
    _bytes.labels(statement_kind).observe(max(0, result_bytes))
