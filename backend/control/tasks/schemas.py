from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from actions.search.schemas import SearchProvider
from actions.shared.cache import CacheOptions


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


TaskPrimitive = Literal["search", "index", "crawl", "schema", "extract", "calibrate"]


class SearchInput(StrictBaseModel):
    query: str
    max_pages: int = Field(default=1, ge=1, le=25)
    provider: SearchProvider = "duckduckgo"
    cache: CacheOptions | None = None


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
