from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from artifacts.models import Artifact
from artifacts.service import BYTE_ARTIFACT_KINDS, is_cache_eligible, warning_count as artifact_warning_count
from crawls.models import Crawl
from crawls.service import _warning_count as crawl_warning_count
from data_schemas.models import DataSchema
from data_schemas.service import task_run_count, warning_count as schema_warning_count
from tasks.models import TaskRunArtifact
from urls.models import Url

from .schemas import HistoryMetricsResponse, MetricBreakdown, MetricCard, MetricDatum

DEFAULT_WINDOW_SECONDS = 6 * 60 * 60


def _cutoff(window_seconds: int) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=window_seconds)


def _sql_like_from_glob(pattern: str) -> str:
    return pattern.replace("%", r"\%").replace("_", r"\_").replace("*", "%")


def _percent(value: float) -> float:
    return round(value * 100, 1)


def _p95(values: list[int]) -> float:
    if not values:
        return 0

    ordered = sorted(values)
    index = max(0, ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index] / 1000, 3)


def _status_class(status_code: int | None) -> str:
    if status_code is None:
        return "none"
    return f"{status_code // 100}xx"


def _top(counter: Counter[str], *, metric: str, label_name: str, limit: int = 10) -> list[MetricDatum]:
    return [
        MetricDatum(
            metric=metric,
            label=label,
            value=value,
            labels={label_name: label},
        )
        for label, value in counter.most_common(limit)
    ]


def crawl_metrics(
    session: Session,
    *,
    url_pattern: str | None = None,
    domain: str | None = None,
    success: bool | None = None,
    status_code: int | None = None,
    warnings: bool | None = None,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> HistoryMetricsResponse:
    statement = (
        select(Crawl)
        .join(Url, Crawl.url_id == Url.id)
        .where(Crawl.started_at >= _cutoff(window_seconds))
    )
    if url_pattern:
        statement = statement.where(Url.normalized_url.ilike(_sql_like_from_glob(url_pattern), escape="\\"))
    if domain:
        statement = statement.where(Url.domain.ilike(f"%{domain}%"))
    if success is not None:
        statement = statement.where(Crawl.success == success)
    if status_code is not None:
        statement = statement.where(Crawl.status_code == status_code)

    crawls = list(session.scalars(statement))
    if warnings is not None:
        crawls = [crawl for crawl in crawls if (crawl_warning_count(crawl) > 0) is warnings]

    total = len(crawls)
    succeeded = sum(1 for crawl in crawls if crawl.success)
    warning_total = sum(crawl_warning_count(crawl) for crawl in crawls)
    durations = [crawl.duration_ms for crawl in crawls if crawl.duration_ms is not None]
    domain_counts = Counter(crawl.url.domain for crawl in crawls)
    status_counts = Counter(_status_class(crawl.status_code) for crawl in crawls)

    return HistoryMetricsResponse(
        window_seconds=window_seconds,
        cards=[
            MetricCard(metric="atlas_crawls_total", label="Crawls", value=total),
            MetricCard(
                metric="atlas_crawl_success_ratio",
                label="Success",
                value=_percent(succeeded / total) if total else 0,
                unit="%",
                tone="warning" if total and succeeded < total else None,
            ),
            MetricCard(
                metric="atlas_crawl_warnings_total",
                label="Warnings",
                value=warning_total,
                tone="warning" if warning_total else None,
            ),
            MetricCard(
                metric="atlas_crawl_duration_seconds_p95",
                label="p95 Latency",
                value=_p95(durations),
                unit="s",
            ),
        ],
        breakdowns=[
            MetricBreakdown(
                metric="atlas_crawls_total",
                label="Crawl Volume By Domain",
                items=_top(domain_counts, metric="atlas_crawls_total", label_name="domain"),
            ),
            MetricBreakdown(
                metric="atlas_crawls_total",
                label="Status Classes",
                items=_top(status_counts, metric="atlas_crawls_total", label_name="status_class", limit=6),
            ),
        ],
    )


def url_metrics(
    session: Session,
    *,
    url_pattern: str | None = None,
    domain: str | None = None,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> HistoryMetricsResponse:
    statement = select(Url)
    if url_pattern:
        statement = statement.where(Url.normalized_url.ilike(_sql_like_from_glob(url_pattern), escape="\\"))
    if domain:
        statement = statement.where(Url.domain.ilike(f"%{domain}%"))

    urls = list(session.scalars(statement))
    url_ids = [url.id for url in urls]
    cutoff = _cutoff(window_seconds)
    crawls = list(session.scalars(select(Crawl).where(Crawl.url_id.in_(url_ids), Crawl.started_at >= cutoff))) if url_ids else []
    artifacts = (
        list(session.scalars(select(Artifact).where(Artifact.url_id.in_(url_ids), Artifact.kind.in_(BYTE_ARTIFACT_KINDS))))
        if url_ids
        else []
    )
    artifact_ids = [artifact.id for artifact in artifacts]
    usages = (
        list(session.scalars(select(TaskRunArtifact).where(TaskRunArtifact.artifact_id.in_(artifact_ids), TaskRunArtifact.created_at >= cutoff)))
        if artifact_ids
        else []
    )
    cache_hits = sum(1 for usage in usages if usage.role == "used")
    produced_artifacts = sum(1 for usage in usages if usage.role == "produced")
    cache_total = cache_hits + produced_artifacts
    recently_crawled_url_ids = {crawl.url_id for crawl in crawls}
    warning_url_ids = {crawl.url_id for crawl in crawls if crawl_warning_count(crawl) > 0}
    warning_url_ids.update(
        artifact.url_id
        for artifact in artifacts
        if artifact.url_id is not None and artifact_warning_count(artifact) > 0
    )
    hours = max(window_seconds / 3600, 1)

    return HistoryMetricsResponse(
        window_seconds=window_seconds,
        cards=[
            MetricCard(metric="atlas_urls_total", label="Known URLs", value=len(urls)),
            MetricCard(metric="atlas_urls_recently_crawled_total", label="Recently Crawled", value=len(recently_crawled_url_ids)),
            MetricCard(metric="atlas_url_visit_rate_per_hour", label="Visit Rate", value=round(len(crawls) / hours, 2), unit="/h"),
            MetricCard(
                metric="atlas_artifact_cache_hit_ratio",
                label="Cache Hit Rate",
                value=_percent(cache_hits / cache_total) if cache_total else 0,
                unit="%",
            ),
            MetricCard(
                metric="atlas_url_warning_density_ratio",
                label="Warning Density",
                value=_percent(len(warning_url_ids) / len(urls)) if urls else 0,
                unit="%",
                tone="warning" if warning_url_ids else None,
            ),
        ],
        breakdowns=[
            MetricBreakdown(
                metric="atlas_urls_total",
                label="URLs By Domain",
                items=_top(Counter(url.domain for url in urls), metric="atlas_urls_total", label_name="domain"),
            ),
            MetricBreakdown(
                metric="atlas_artifact_cache_events_total",
                label="Cache Events",
                items=[
                    MetricDatum(metric="atlas_artifact_cache_events_total", label="Hits", value=cache_hits, labels={"event": "hit"}),
                    MetricDatum(metric="atlas_artifact_cache_events_total", label="Fresh", value=produced_artifacts, labels={"event": "fresh"}),
                ],
            ),
        ],
    )


def artifact_metrics(
    session: Session,
    *,
    url_pattern: str | None = None,
    kind: str | None = None,
    invalidated: bool | None = None,
    warnings: bool | None = None,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> HistoryMetricsResponse:
    statement = select(Artifact).outerjoin(Url, Artifact.url_id == Url.id).where(Artifact.kind.in_(BYTE_ARTIFACT_KINDS))
    if url_pattern:
        statement = statement.where(Url.normalized_url.ilike(_sql_like_from_glob(url_pattern), escape="\\"))
    if kind:
        statement = statement.where(Artifact.kind == kind)
    if invalidated is True:
        statement = statement.where(Artifact.invalidated_at.is_not(None))
    if invalidated is False:
        statement = statement.where(Artifact.invalidated_at.is_(None))

    artifacts = list(session.scalars(statement))
    if warnings is not None:
        artifacts = [artifact for artifact in artifacts if (artifact_warning_count(artifact) > 0) is warnings]

    active = [artifact for artifact in artifacts if artifact.invalidated_at is None]
    invalidated_count = len(artifacts) - len(active)
    warned = [artifact for artifact in active if artifact_warning_count(artifact) > 0]
    eligible = [artifact for artifact in active if is_cache_eligible(artifact)]
    bytes_by_kind: defaultdict[str, int] = defaultdict(int)
    count_by_kind: Counter[str] = Counter()
    for artifact in artifacts:
        bytes_by_kind[artifact.kind] += artifact.size_bytes
        count_by_kind[artifact.kind] += 1

    return HistoryMetricsResponse(
        window_seconds=window_seconds,
        cards=[
            MetricCard(metric="atlas_artifacts_total", label="Artifacts", value=len(artifacts)),
            MetricCard(metric="atlas_artifacts_cache_eligible_total", label="Cache Eligible", value=len(eligible)),
            MetricCard(
                metric="atlas_artifacts_warning_total",
                label="Active Warned",
                value=len(warned),
                tone="warning" if warned else None,
            ),
            MetricCard(metric="atlas_artifacts_invalidated_total", label="Invalidated", value=invalidated_count),
            MetricCard(metric="atlas_artifact_bytes_total", label="Storage", value=sum(artifact.size_bytes for artifact in artifacts), unit="bytes"),
        ],
        breakdowns=[
            MetricBreakdown(
                metric="atlas_artifact_bytes_total",
                label="Bytes By Kind",
                unit="bytes",
                items=[
                    MetricDatum(metric="atlas_artifact_bytes_total", label=kind, value=value, labels={"kind": kind})
                    for kind, value in sorted(bytes_by_kind.items(), key=lambda item: item[1], reverse=True)
                ],
            ),
            MetricBreakdown(
                metric="atlas_artifacts_total",
                label="Artifacts By Kind",
                items=_top(count_by_kind, metric="atlas_artifacts_total", label_name="kind", limit=8),
            ),
        ],
    )


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
        statement = statement.where(DataSchema.match.ilike(_sql_like_from_glob(match_pattern), escape="\\"))
    if prompt:
        statement = statement.where(DataSchema.prompt.ilike(f"%{prompt}%"))
    if schema_type:
        statement = statement.where(DataSchema.schema_type == schema_type)
    if enabled is not None:
        statement = statement.where(DataSchema.enabled == enabled)

    schemas = list(session.scalars(statement))
    if warnings is not None:
        schemas = [schema for schema in schemas if (schema_warning_count(schema) > 0) is warnings]

    use_counts = {schema.id: task_run_count(session, schema.id) for schema in schemas}
    total_uses = sum(use_counts.values())
    reused_uses = sum(max(count - 1, 0) for count in use_counts.values())
    failing = [schema for schema in schemas if schema.failure_count > 0 or schema_warning_count(schema) > 0]
    match_counts = Counter({schema.match: use_counts[schema.id] for schema in schemas if use_counts[schema.id] > 0})
    type_counts = Counter(schema.schema_type for schema in schemas)

    return HistoryMetricsResponse(
        window_seconds=window_seconds,
        cards=[
            MetricCard(metric="atlas_data_schemas_total", label="Schemas", value=len(schemas)),
            MetricCard(metric="atlas_data_schemas_enabled_total", label="Enabled", value=sum(1 for schema in schemas if schema.enabled)),
            MetricCard(metric="atlas_data_schema_uses_total", label="Uses", value=total_uses),
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
                items=_top(match_counts, metric="atlas_data_schema_uses_total", label_name="match", limit=10),
            ),
            MetricBreakdown(
                metric="atlas_data_schemas_total",
                label="Schemas By Type",
                items=_top(type_counts, metric="atlas_data_schemas_total", label_name="schema_type", limit=6),
            ),
        ],
    )
