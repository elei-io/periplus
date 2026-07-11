from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from repository.ducklake import Catalogue, CatalogueService, catalogue_config_from_env

from .cluster import collect_cluster_metrics
from .schemas import (
    ClusterOperationsMetrics,
    CrawlHealthMetrics,
    DomainHealthMetrics,
    FailureReasonMetrics,
    OperationsMetricsResponse,
    PermitMetrics,
    PrometheusOperationsMetrics,
    TaskHealthMetrics,
    TaskStateMetrics,
)

SUPPORTED_WINDOWS = {900, 3600, 21600, 86400}


def _number(value) -> float:
    return round(float(value or 0), 3)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def collect_operations_metrics(
    session: Session,
    *,
    window_seconds: int,
) -> OperationsMetricsResponse:
    if window_seconds not in SUPPORTED_WINDOWS:
        raise ValueError("Unsupported metrics window.")
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=window_seconds)
    cluster = collect_cluster_metrics(session, now=now)

    task_statuses: dict[str, dict[str, int]] = {}

    task_states: list[TaskStateMetrics] = []
    for state in cluster.tasks:
        statuses = task_statuses.get(state.primitive, {})
        task_states.append(
            TaskStateMetrics(
                primitive=state.primitive,
                queued=state.queued,
                running=state.running,
                succeeded=statuses.get("succeeded", 0),
                failed=statuses.get("failed", 0),
                cancelled=statuses.get("cancelled", 0),
            )
        )

    all_statuses = {
        status: sum(statuses.get(status, 0) for statuses in task_statuses.values())
        for status in ("succeeded", "failed", "cancelled", "skipped")
    }
    terminal_runs = sum(all_statuses.values())
    decided_runs = all_statuses["succeeded"] + all_statuses["failed"]
    queue_p50 = queue_p95 = execution_p50 = execution_p95 = 0.0

    with Catalogue(catalogue_config_from_env()) as catalogue:
        durable_health = CatalogueService(catalogue).crawl_health_since(cutoff)
    crawl_summary = durable_health["summary"]
    crawl_total = int(crawl_summary["total"] or 0)
    crawl_succeeded = int(crawl_summary["succeeded"] or 0)
    crawl_p50 = _number(crawl_summary["duration_p50_seconds"])
    crawl_p95 = _number(crawl_summary["duration_p95_seconds"])

    domains = [
        DomainHealthMetrics(
            domain=str(row["domain"] or "unknown"),
            total=int(row["total"]),
            failed=int(row["failed"]),
            success_ratio=_ratio(
                int(row["total"]) - int(row["failed"]), int(row["total"])
            ),
            duration_p95_seconds=_number(row["duration_p95_seconds"]),
        )
        for row in durable_health["domains"]
    ]

    return OperationsMetricsResponse(
        generated_at=now,
        window_seconds=window_seconds,
        cluster=ClusterOperationsMetrics(
            workers_live=cluster.workers_live,
            workers_stale=cluster.workers_stale,
            worker_capacity=cluster.worker_capacity,
            worker_active_runs=cluster.worker_active_runs,
            oldest_live_worker_heartbeat_age_seconds=(
                cluster.oldest_live_worker_heartbeat_age_seconds
            ),
            oldest_queued_age_seconds=cluster.oldest_queued_age_seconds,
            tasks=task_states,
            permits=[PermitMetrics(**permit.__dict__) for permit in cluster.permits],
        ),
        tasks=TaskHealthMetrics(
            terminal_runs=terminal_runs,
            succeeded=all_statuses["succeeded"],
            failed=all_statuses["failed"],
            cancelled=all_statuses["cancelled"],
            success_ratio=_ratio(all_statuses["succeeded"], decided_runs),
            queue_p50_seconds=queue_p50,
            queue_p95_seconds=queue_p95,
            execution_p50_seconds=execution_p50,
            execution_p95_seconds=execution_p95,
        ),
        crawls=CrawlHealthMetrics(
            total=crawl_total,
            succeeded=crawl_succeeded,
            failed=crawl_total - crawl_succeeded,
            success_ratio=_ratio(crawl_succeeded, crawl_total),
            duration_p50_seconds=crawl_p50,
            duration_p95_seconds=crawl_p95,
        ),
        domains=domains,
        failures=[
            FailureReasonMetrics(reason=str(row["reason"]), count=int(row["count"]))
            for row in durable_health["failures"]
        ],
        prometheus=PrometheusOperationsMetrics(configured=False, available=False),
    )
