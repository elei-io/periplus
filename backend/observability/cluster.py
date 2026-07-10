from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

import db.models  # noqa: F401  # Ensure relationship targets are registered for standalone use.
from crawl_policies.models import CrawlPermit, CrawlPolicy
from tasks.heartbeats import worker_heartbeat_retention_seconds
from tasks.models import Task, TaskRun, WorkerHeartbeat

_PRIMITIVES = ("search", "index", "crawl", "schema", "extract", "calibrate")


@dataclass(frozen=True)
class TaskStateSnapshot:
    primitive: str
    queued: int
    running: int


@dataclass(frozen=True)
class PermitSnapshot:
    scope: str
    policy: str
    match: str | None
    in_use: int
    capacity: int


@dataclass(frozen=True)
class ClusterMetricsSnapshot:
    workers_live: int
    workers_stale: int
    worker_capacity: int
    worker_active_runs: int
    oldest_live_worker_heartbeat_age_seconds: float
    oldest_queued_age_seconds: float
    tasks: tuple[TaskStateSnapshot, ...]
    permits: tuple[PermitSnapshot, ...]


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _policy_limit(policy: CrawlPolicy) -> int | None:
    value = (policy.config or {}).get("max_concurrency")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def collect_cluster_metrics(
    session: Session,
    *,
    now: datetime | None = None,
    browser_capacity: int | None = None,
    heartbeat_seconds: int | None = None,
) -> ClusterMetricsSnapshot:
    now = now or datetime.now(UTC)
    browser_capacity = browser_capacity or _env_int("ATLAS_BROWSER_CONCURRENCY", 12)
    heartbeat_seconds = heartbeat_seconds or _env_int("ATLAS_WORKER_HEARTBEAT_SECONDS", 10)
    stale_before = now - timedelta(seconds=heartbeat_seconds * 2)
    retention_after = now - timedelta(seconds=worker_heartbeat_retention_seconds())

    session.execute(text("SET LOCAL statement_timeout = '2s'"))
    task_rows = session.execute(
        select(Task.primitive, TaskRun.status, func.count())
        .join(TaskRun, TaskRun.task_id == Task.id)
        .where(TaskRun.status.in_(("queued", "running")))
        .group_by(Task.primitive, TaskRun.status)
    )
    task_counts: dict[str, dict[str, int]] = {}
    for primitive, status, count in task_rows:
        task_counts.setdefault(primitive, {})[status] = int(count)
    primitives = set(_PRIMITIVES) | set(task_counts)
    tasks = tuple(
        TaskStateSnapshot(
            primitive=primitive,
            queued=task_counts.get(primitive, {}).get("queued", 0),
            running=task_counts.get(primitive, {}).get("running", 0),
        )
        for primitive in sorted(primitives)
    )

    oldest_queued_at = session.scalar(
        select(func.min(TaskRun.queued_at)).where(TaskRun.status == "queued")
    )
    oldest_queued_age = max(0.0, (now - oldest_queued_at).total_seconds()) if oldest_queued_at else 0.0

    workers = list(session.scalars(select(WorkerHeartbeat)))
    live = [worker for worker in workers if not worker.stopping and worker.last_seen_at >= stale_before]
    stale = [
        worker
        for worker in workers
        if not worker.stopping and retention_after <= worker.last_seen_at < stale_before
    ]
    oldest_live_heartbeat_age = (
        max((now - worker.last_seen_at).total_seconds() for worker in live) if live else 0.0
    )

    active_permits = list(
        session.scalars(select(CrawlPermit).where(CrawlPermit.leased_until > now))
    )
    browser_in_use = sum(permit.permit_key == "browser:global" for permit in active_permits)
    permits: list[PermitSnapshot] = [
        PermitSnapshot("browser", "", None, browser_in_use, browser_capacity)
    ]
    policy_counts: dict[object, int] = {}
    for permit in active_permits:
        if permit.policy_id is not None:
            policy_counts[permit.policy_id] = policy_counts.get(permit.policy_id, 0) + 1
    policies = list(session.scalars(select(CrawlPolicy).where(CrawlPolicy.enabled.is_(True))))
    for policy in policies:
        limit = _policy_limit(policy)
        if limit is not None:
            permits.append(
                PermitSnapshot(
                    "policy",
                    policy.metric_slug,
                    policy.match,
                    policy_counts.get(policy.id, 0),
                    limit,
                )
            )

    return ClusterMetricsSnapshot(
        workers_live=len(live),
        workers_stale=len(stale),
        worker_capacity=sum(worker.capacity for worker in live),
        worker_active_runs=sum(worker.active_run_count for worker in live),
        oldest_live_worker_heartbeat_age_seconds=max(0.0, oldest_live_heartbeat_age),
        oldest_queued_age_seconds=oldest_queued_age,
        tasks=tasks,
        permits=tuple(permits),
    )
