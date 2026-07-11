from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from repository.ducklake import Catalogue, CatalogueService, catalogue_config_from_env
from tasks.queue import TaskRunState
from tasks.schemas import TaskOperationsRecord

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
    task_runs: list[TaskRunState] | None = None,
    task_summary: TaskOperationsRecord | None = None,
) -> OperationsMetricsResponse:
    if window_seconds not in SUPPORTED_WINDOWS:
        raise ValueError("Unsupported metrics window.")
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=window_seconds)
    cluster = collect_cluster_metrics(session, now=now)
    task_runs = task_runs or []

    task_statuses: dict[str, dict[str, int]] = {}
    for run in task_runs:
        if run.finished_at is not None and run.finished_at >= cutoff:
            statuses = task_statuses.setdefault(run.primitive, {})
            statuses[run.status] = statuses.get(run.status, 0) + 1

    task_states: list[TaskStateMetrics] = []
    primitives = {state.primitive for state in cluster.tasks} | {run.primitive for run in task_runs}
    for primitive in sorted(primitives):
        statuses = task_statuses.get(primitive, {})
        task_states.append(
            TaskStateMetrics(
                primitive=primitive,
                queued=sum(run.primitive == primitive and run.status == "queued" for run in task_runs),
                running=sum(run.primitive == primitive and run.status == "running" for run in task_runs),
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
    def percentile(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]

    queue_values = [(run.started_at - run.queued_at).total_seconds() for run in task_runs if run.started_at is not None and run.started_at >= cutoff]
    execution_values = [(run.finished_at - run.started_at).total_seconds() for run in task_runs if run.started_at is not None and run.finished_at is not None and run.finished_at >= cutoff]
    queue_p50, queue_p95 = percentile(queue_values, 0.5), percentile(queue_values, 0.95)
    execution_p50, execution_p95 = percentile(execution_values, 0.5), percentile(execution_values, 0.95)

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
            workers_live=len(task_summary.workers) if task_summary is not None else 0,
            workers_stale=task_summary.stale_workers if task_summary is not None else 0,
            worker_capacity=sum(worker.capacity for worker in task_summary.workers) if task_summary is not None else 0,
            worker_active_runs=sum(worker.active_run_count for worker in task_summary.workers) if task_summary is not None else 0,
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
