from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from actions.extract.schemas import Input as ExtractInput
from actions.index.schemas import Input as IndexInput
from actions.search.schemas import SearchProvider
from actions.shared.data_schema.schemas import Input as SchemaInput
from actions.crawl.schemas import Input as CrawlInput
from actions.calibrate.schemas import Input as CalibrateInput
from actions.shared.cache import CacheOptions


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


TaskPrimitive = Literal["search", "index", "crawl", "schema", "extract", "calibrate"]
TaskRunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "skipped"]
TaskRunTriggerKind = Literal["scheduled", "manual", "retry", "backfill"]


class SearchInput(StrictBaseModel):
    query: str
    max_pages: int = Field(default=1, ge=1, le=25)
    provider: SearchProvider = "duckduckgo"
    cache: CacheOptions | None = None


TaskInputJson = Annotated[
    SearchInput | IndexInput | CrawlInput | SchemaInput | ExtractInput | CalibrateInput,
    Field(union_mode="left_to_right"),
]


class OnceSchedule(StrictBaseModel):
    kind: Literal["once"] = "once"
    run_at: datetime
    timezone: str = "UTC"


class CronSchedule(StrictBaseModel):
    kind: Literal["cron"] = "cron"
    expr: str
    timezone: str = "UTC"
    start_at: datetime | None = None
    end_at: datetime | None = None


class IntervalSchedule(StrictBaseModel):
    kind: Literal["interval"] = "interval"
    every_seconds: int = Field(gt=0)
    timezone: str = "UTC"
    start_at: datetime | None = None
    end_at: datetime | None = None


TaskScheduleJson = Annotated[
    OnceSchedule | CronSchedule | IntervalSchedule,
    Field(discriminator="kind"),
]


class TaskCreate(StrictBaseModel):
    name: str
    primitive: TaskPrimitive
    input: dict[str, Any]
    schedule: TaskScheduleJson | None = None
    identity_key: str | None = None


class TaskUpdate(StrictBaseModel):
    name: str | None = None
    primitive: TaskPrimitive | None = None
    input: dict[str, Any] | None = None
    schedule: TaskScheduleJson | None = None
    identity_key: str | None = None
    archived_at: datetime | None = None
    archived_reason: str | None = None


class TaskWarningsJson(StrictBaseModel):
    codes: list[str] = Field(default_factory=list)
    count: int = 0
    path: str | None = None


class TaskRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    primitive: TaskPrimitive
    input_json: dict[str, Any]
    revision: int
    schedule_json: dict[str, Any] | None = None
    identity_key: str | None = None
    archived_at: datetime | None = None
    archived_reason: str | None = None
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TaskRunRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    task_revision: int
    primitive: TaskPrimitive
    status: TaskRunStatus
    trigger_kind: TaskRunTriggerKind
    data_schema_id: UUID | None = None
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancellation_requested_at: datetime | None = None
    cancelled_at: datetime | None = None
    retry_at: datetime | None = None
    attempt: int
    failed_attempts: int
    max_attempts: int
    input_json: dict[str, Any]
    warnings_json: dict[str, Any]
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskRunSubmission(BaseModel):
    task_id: UUID
    run_id: UUID
    status: Literal["queued"] = "queued"


class WorkerHeartbeatRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    worker_id: str
    started_at: datetime
    last_seen_at: datetime
    capacity: int
    active_run_count: int
    stopping: bool
    version: str | None = None


class TaskOperationsRecord(BaseModel):
    queued: int
    running: int
    cancelling: int
    stale_workers: int
    oldest_queued_at: datetime | None = None
    average_queue_latency_seconds: float | None = None
    average_execution_seconds: float | None = None
    nats_available: bool | None = None
    workers: list[WorkerHeartbeatRecord]


class WorkerHeartbeatPurgeRecord(BaseModel):
    deleted: int

