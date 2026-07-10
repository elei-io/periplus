from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TaskStateMetrics(BaseModel):
    primitive: str
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0


class PermitMetrics(BaseModel):
    scope: str
    policy: str
    match: str | None = None
    in_use: int
    capacity: int


class ClusterOperationsMetrics(BaseModel):
    workers_live: int
    workers_stale: int
    worker_capacity: int
    worker_active_runs: int
    oldest_live_worker_heartbeat_age_seconds: float
    oldest_queued_age_seconds: float
    tasks: list[TaskStateMetrics] = Field(default_factory=list)
    permits: list[PermitMetrics] = Field(default_factory=list)


class TaskHealthMetrics(BaseModel):
    terminal_runs: int
    succeeded: int
    failed: int
    cancelled: int
    success_ratio: float
    queue_p50_seconds: float
    queue_p95_seconds: float
    execution_p50_seconds: float
    execution_p95_seconds: float


class CrawlHealthMetrics(BaseModel):
    total: int
    succeeded: int
    failed: int
    success_ratio: float
    duration_p50_seconds: float
    duration_p95_seconds: float


class DomainHealthMetrics(BaseModel):
    domain: str
    total: int
    failed: int
    success_ratio: float
    duration_p95_seconds: float


class FailureReasonMetrics(BaseModel):
    reason: str
    count: int


class PrometheusOperationsMetrics(BaseModel):
    configured: bool
    available: bool
    capacity_waiters: float | None = None
    capacity_wait_p95_seconds: float | None = None
    page_acquisitions_per_second: float | None = None
    page_success_ratio: float | None = None
    dropped_observations: float | None = None
    error: str | None = None


class OperationsMetricsResponse(BaseModel):
    generated_at: datetime
    window_seconds: int
    cluster: ClusterOperationsMetrics
    tasks: TaskHealthMetrics
    crawls: CrawlHealthMetrics
    domains: list[DomainHealthMetrics] = Field(default_factory=list)
    failures: list[FailureReasonMetrics] = Field(default_factory=list)
    prometheus: PrometheusOperationsMetrics
