"""Prometheus metrics for source-owned materialization workloads."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

_source_items = Counter(
    "atlas_materialization_source_items_total",
    "Source items selected by fixed materialization workloads.",
    ("workload",),
)
_source_bytes = Counter(
    "atlas_materialization_source_bytes_total",
    "Logical source bytes selected by fixed materialization workloads.",
    ("workload",),
)
_output_rows = Counter(
    "atlas_materialization_output_rows_total",
    "Rows committed by fixed materialization workloads.",
    ("workload",),
)
_projection_bytes = Counter(
    "atlas_materialization_projection_bytes_total",
    "Logical Arrow bytes produced by fixed materialization workloads.",
    ("workload",),
)
_phase_duration = Histogram(
    "atlas_materialization_phase_duration_seconds",
    "Fixed materialization workload phase duration.",
    ("workload", "phase"),
)


def stage(
    *,
    workload: str,
    source_items: int,
    source_bytes: int,
    output_rows: int,
    projection_bytes: int,
    select_seconds: float,
    project_seconds: float,
    write_seconds: float,
    elapsed_seconds: float,
) -> None:
    _source_items.labels(workload).inc(max(0, source_items))
    _source_bytes.labels(workload).inc(max(0, source_bytes))
    _output_rows.labels(workload).inc(max(0, output_rows))
    _projection_bytes.labels(workload).inc(max(0, projection_bytes))
    for phase, seconds in (
        ("select", select_seconds),
        ("project", project_seconds),
        ("write", write_seconds),
        ("total", elapsed_seconds),
    ):
        _phase_duration.labels(workload, phase).observe(max(0.0, seconds))
