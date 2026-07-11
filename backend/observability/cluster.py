from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

import db.models  # noqa: F401  # Ensure relationship targets are registered for standalone use.
from crawl_policies.models import CrawlPolicy

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
) -> ClusterMetricsSnapshot:
    now = now or datetime.now(UTC)
    browser_capacity = browser_capacity or _env_int("ATLAS_BROWSER_CONCURRENCY", 12)

    session.execute(text("SET LOCAL statement_timeout = '2s'"))
    task_counts: dict[str, dict[str, int]] = {}
    primitives = set(_PRIMITIVES)
    tasks = tuple(
        TaskStateSnapshot(
            primitive=primitive,
            queued=task_counts.get(primitive, {}).get("queued", 0),
            running=task_counts.get(primitive, {}).get("running", 0),
        )
        for primitive in sorted(primitives)
    )

    oldest_queued_age = 0.0

    permits: list[PermitSnapshot] = [
        PermitSnapshot("browser", "", None, 0, browser_capacity)
    ]
    policies = list(session.scalars(select(CrawlPolicy).where(CrawlPolicy.enabled.is_(True))))
    for policy in policies:
        limit = _policy_limit(policy)
        if limit is not None:
            permits.append(
                PermitSnapshot(
                    "policy",
                    policy.metric_slug,
                    policy.match,
                    0,
                    limit,
                )
            )

    return ClusterMetricsSnapshot(
        workers_live=0,
        workers_stale=0,
        worker_capacity=0,
        worker_active_runs=0,
        oldest_live_worker_heartbeat_age_seconds=0.0,
        oldest_queued_age_seconds=oldest_queued_age,
        tasks=tasks,
        permits=tuple(permits),
    )
