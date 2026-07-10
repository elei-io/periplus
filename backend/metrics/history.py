from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from data_schemas.models import DataSchema
from data_schemas.service import task_run_count, warning_count as schema_warning_count

from .schemas import HistoryMetricsResponse, MetricBreakdown, MetricCard, MetricDatum

DEFAULT_WINDOW_SECONDS = 6 * 60 * 60


def _sql_like_from_glob(pattern: str) -> str:
    return pattern.replace("%", r"\%").replace("_", r"\_").replace("*", "%")


def _percent(value: float) -> float:
    return round(value * 100, 1)


def _top(
    counter: Counter[str],
    *,
    metric: str,
    label_name: str,
    limit: int = 10,
) -> list[MetricDatum]:
    return [
        MetricDatum(
            metric=metric,
            label=label,
            value=value,
            labels={label_name: label},
        )
        for label, value in counter.most_common(limit)
    ]


def data_schema_metrics(
    session: Session,
    *,
    match_pattern: str | None = None,
    prompt: str | None = None,
    schema_type: str | None = None,
    enabled: bool | None = None,
    warnings: bool | None = None,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> HistoryMetricsResponse:
    statement = select(DataSchema)
    if match_pattern:
        statement = statement.where(
            DataSchema.match.ilike(_sql_like_from_glob(match_pattern), escape="\\")
        )
    if prompt:
        statement = statement.where(DataSchema.prompt.ilike(f"%{prompt}%"))
    if schema_type:
        statement = statement.where(DataSchema.schema_type == schema_type)
    if enabled is not None:
        statement = statement.where(DataSchema.enabled == enabled)

    schemas = list(session.scalars(statement))
    if warnings is not None:
        schemas = [
            schema
            for schema in schemas
            if (schema_warning_count(schema) > 0) is warnings
        ]

    use_counts = {schema.id: task_run_count(session, schema.id) for schema in schemas}
    total_uses = sum(use_counts.values())
    reused_uses = sum(max(count - 1, 0) for count in use_counts.values())
    failing = [
        schema
        for schema in schemas
        if schema.failure_count > 0 or schema_warning_count(schema) > 0
    ]
    match_counts = Counter(
        {
            schema.match: use_counts[schema.id]
            for schema in schemas
            if use_counts[schema.id] > 0
        }
    )
    type_counts = Counter(schema.schema_type for schema in schemas)

    return HistoryMetricsResponse(
        window_seconds=window_seconds,
        cards=[
            MetricCard(
                metric="atlas_data_schemas_total",
                label="Schemas",
                value=len(schemas),
            ),
            MetricCard(
                metric="atlas_data_schemas_enabled_total",
                label="Enabled",
                value=sum(1 for schema in schemas if schema.enabled),
            ),
            MetricCard(
                metric="atlas_data_schema_uses_total",
                label="Uses",
                value=total_uses,
            ),
            MetricCard(
                metric="atlas_data_schema_reuse_ratio",
                label="Reuse Rate",
                value=_percent(reused_uses / total_uses) if total_uses else 0,
                unit="%",
            ),
            MetricCard(
                metric="atlas_data_schema_failures_total",
                label="Troubled",
                value=len(failing),
                tone="warning" if failing else None,
            ),
        ],
        breakdowns=[
            MetricBreakdown(
                metric="atlas_data_schema_uses_total",
                label="Top Match Patterns",
                items=_top(
                    match_counts,
                    metric="atlas_data_schema_uses_total",
                    label_name="match",
                ),
            ),
            MetricBreakdown(
                metric="atlas_data_schemas_total",
                label="Schemas By Type",
                items=_top(
                    type_counts,
                    metric="atlas_data_schemas_total",
                    label_name="schema_type",
                    limit=6,
                ),
            ),
        ],
    )
