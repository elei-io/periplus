from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from actions.extract.schemas import Input as ExtractInput
from actions.index.schemas import Input as IndexInput
from actions.search.schemas import SearchProvider
from actions.shared.quality.schemas import QualityWarning
from actions.shared.data_schema.schemas import Input as SchemaInput
from actions.crawl.schemas import Input as CrawlInput
from actions.calibrate.schemas import Input as CalibrateInput
from actions.shared.cache import CacheOptions


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


TaskPrimitive = Literal["search", "index", "crawl", "schema", "extract", "calibrate"]
TaskRunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "skipped"]
TaskRunTriggerKind = Literal["scheduled", "manual", "effect", "retry", "backfill"]
EffectRunStatus = Literal["running", "applied", "skipped", "failed"]
TaskOrigin = Literal["human", "effect"]
TaskEffectType = Literal[
    "create_task",
    "upsert_task",
    "update_task",
    "archive_task",
    "enqueue_run",
]
EffectRunOperation = TaskEffectType | Literal["noop"]


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


class TaskTemplate(StrictBaseModel):
    name: str | None = None
    primitive: TaskPrimitive
    input: dict[str, Any]
    schedule: TaskScheduleJson | None = None
    identity_key: str | None = None


class TaskWhere(StrictBaseModel):
    id: UUID | None = None
    identity_key: str | None = None
    identity_key_template: str | None = None


class EffectCondition(StrictBaseModel):
    warnings_include: list[str] = Field(default_factory=list)
    warnings_exclude: list[str] = Field(default_factory=list)
    output_path_exists: str | None = None


class CreateTaskEffect(StrictBaseModel):
    type: Literal["create_task"] = "create_task"
    task_template: TaskTemplate


class UpsertTaskEffect(StrictBaseModel):
    type: Literal["upsert_task"] = "upsert_task"
    source_path: str | None = None
    task_template: TaskTemplate
    on_existing: Literal["keep", "update", "replace", "enable"] = "keep"


class UpdateTaskEffect(StrictBaseModel):
    type: Literal["update_task"] = "update_task"
    where: TaskWhere
    patch: dict[str, Any]
    when: EffectCondition | None = None


class ArchiveTaskEffect(StrictBaseModel):
    type: Literal["archive_task"] = "archive_task"
    where: TaskWhere
    when: EffectCondition | None = None


class EnqueueTarget(StrictBaseModel):
    kind: Literal["task", "tasks_from_source_path", "upserted_task"] = "task"
    task_id: UUID | None = None
    identity_key: str | None = None
    source_path: str | None = None
    identity_key_template: str | None = None


class EnqueueDedupe(StrictBaseModel):
    policy: Literal[
        "always",
        "if_not_queued",
        "if_not_queued_or_running",
        "if_not_succeeded_since",
    ] = "if_not_queued_or_running"
    since: datetime | None = None


class EnqueueRunEffect(StrictBaseModel):
    type: Literal["enqueue_run"] = "enqueue_run"
    target: EnqueueTarget
    dedupe: EnqueueDedupe = Field(default_factory=EnqueueDedupe)
    trigger_kind: TaskRunTriggerKind = "effect"
    when: EffectCondition | None = None


TaskEffectJson = Annotated[
    CreateTaskEffect
    | UpsertTaskEffect
    | UpdateTaskEffect
    | ArchiveTaskEffect
    | EnqueueRunEffect,
    Field(discriminator="type"),
]


class CrawlPageOutput(StrictBaseModel):
    url: str
    success: bool
    crawl_id: UUID | None = None
    document_id: str | None = None
    repository_snapshot: int | None = None
    quality_warnings: list[QualityWarning] = Field(default_factory=list)


class CatalogueResultReference(StrictBaseModel):
    run_id: UUID


class BoundedTaskOutputJson(StrictBaseModel):
    version: Literal[1] = 1
    primitive: TaskPrimitive
    status: Literal["succeeded"] = "succeeded"
    counts: dict[str, int] = Field(default_factory=dict)
    catalogue: CatalogueResultReference | None


TaskOutputJson = BoundedTaskOutputJson


class TaskWarningsJson(StrictBaseModel):
    codes: list[str] = Field(default_factory=list)
    count: int = 0
    path: str | None = None


class EffectRunInputJson(StrictBaseModel):
    item: dict[str, Any] | None = None
    source_path: str | None = None
    rendered_template: dict[str, Any] = Field(default_factory=dict)


class EffectRunOutputJson(StrictBaseModel):
    status: EffectRunStatus
    operation: EffectRunOperation
    target_task_id: UUID | None = None
    target_run_id: UUID | None = None
    identity_key: str | None = None
    reason: str | None = None


class TaskRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    primitive: TaskPrimitive
    input_json: dict[str, Any]
    revision: int
    schedule_json: dict[str, Any] | None = None
    identity_key: str | None = None
    created_by_effect_run_id: UUID | None = None
    updated_by_effect_run_id: UUID | None = None
    archived_by_effect_run_id: UUID | None = None
    archived_at: datetime | None = None
    archived_reason: str | None = None
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TaskEffectRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    effect_type: TaskEffectType
    config_json: dict[str, Any]
    enabled: bool
    position: int
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
    triggered_by_effect_run_id: UUID | None = None
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


class EffectRunRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    effect_id: UUID
    source_run_id: UUID
    status: EffectRunStatus
    operation: EffectRunOperation
    target_task_id: UUID | None = None
    target_run_id: UUID | None = None
    input_json: dict[str, Any]
    output_json: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
