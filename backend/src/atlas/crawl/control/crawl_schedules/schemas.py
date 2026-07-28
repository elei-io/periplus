from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)
from atlas.crawl.control.crawl_graphs.schemas import (
    DEFAULT_GRAPH_RUN_MAX_CRAWLS,
    MAX_GRAPH_RUN_CRAWLS,
)


class IntervalTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["interval"]
    seconds: int = Field(ge=60, le=366 * 24 * 60 * 60)


class CronTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["cron"]
    expression: str = Field(min_length=1, max_length=200)
    timezone: str = Field(default="UTC", min_length=1, max_length=200)


ScheduleTiming = Annotated[
    IntervalTiming | CronTiming,
    Field(discriminator="kind"),
]
schedule_timing_adapter = TypeAdapter(ScheduleTiming)

OverlapPolicy = Literal["skip", "allow"]
MisfirePolicy = Literal["skip", "run_once"]
ScheduleStatus = Literal[
    "active",
    "paused",
    "not_started",
    "exhausted",
    "ended",
]


class ScheduleWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Schedule datetimes must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> ScheduleWindow:
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("Schedule end must be after its start.")
        return self


class CrawlScheduleInput(ScheduleWindow):
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    timing: ScheduleTiming
    maximum_run_count: int | None = Field(default=None, ge=1)
    max_crawls: int = Field(
        default=DEFAULT_GRAPH_RUN_MAX_CRAWLS,
        ge=1,
        le=MAX_GRAPH_RUN_CRAWLS,
    )
    root_urls: list[str] = Field(min_length=1, max_length=10_000)
    overlap_policy: OverlapPolicy = "skip"
    misfire_policy: MisfirePolicy = "skip"


class CrawlScheduleCreate(CrawlScheduleInput):
    pass


class CrawlScheduleUpdate(CrawlScheduleInput):
    pass


class CrawlScheduleRecord(CrawlScheduleInput):
    id: UUID
    graph_id: UUID
    status: ScheduleStatus
    run_count: int
    next_run_at: datetime | None
    last_occurrence_at: datetime | None
    last_run_id: UUID | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class CrawlScheduleList(BaseModel):
    items: list[CrawlScheduleRecord]
    total: int


class CrawlScheduleResource(CrawlScheduleRecord):
    graph_slug: str


class CrawlScheduleResourceList(BaseModel):
    items: list[CrawlScheduleResource]
    total: int


class SchedulePreviewRequest(ScheduleWindow):
    timing: ScheduleTiming
    count: int = Field(default=5, ge=1, le=20)


class SchedulePreviewResponse(BaseModel):
    occurrences: list[datetime]
