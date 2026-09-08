from datetime import UTC, datetime, timedelta
import math
import re
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter, CroniterBadDateError
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from periplus.crawl.control.collections.schemas import CollectionSpec
from periplus.crawl.runtime.selection_sql import validate_follow_sql


class DefinitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    specification: CollectionSpec
    priority: int = Field(default=0, ge=-10, le=10)

    @model_validator(mode="after")
    def valid_intent(self):
        spec = self.specification
        if not self.name.strip() or not (spec.seed_urls or spec.seed_description or spec.seed_sql):
            raise ValueError("Name and starting URLs, description or seed SQL are required")
        if spec.origin is not None:
            raise ValueError("Reusable intent cannot contain an execution origin")
        validate_follow_sql(spec.follow_sql)
        return self


class DefinitionView(DefinitionInput):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    version: int
    created_at: datetime


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["interval", "cron"]
    interval_seconds: int | None = Field(default=None, ge=60, le=31536000)
    cron: str | None = Field(default=None, max_length=100)
    timezone: str = Field(default="UTC", max_length=100)
    start_at: AwareDatetime
    stop_at: AwareDatetime | None = None
    max_count: int | None = Field(default=None, ge=1, le=1000000)
    enabled: bool = True

    @model_validator(mode="after")
    def valid_schedule(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use an IANA timezone such as UTC or Asia/Tokyo") from exc
        if self.stop_at is not None and self.stop_at <= self.start_at:
            raise ValueError("Stop must be later than start")
        if self.kind == "interval":
            if self.interval_seconds is None or self.cron is not None:
                raise ValueError("Interval requires seconds and no cron expression")
        elif self.interval_seconds is not None or not self.cron or len(self.cron.split()) != 5 or not croniter.is_valid(self.cron):
            raise ValueError("Cron requires a valid five-field expression and no interval")
        if self.cron and re.fullmatch(r"[0-9*,/\s-]+", self.cron) is None:
            # Numeric standard cron only: disallow randomized and extended forms.
            raise ValueError("Use numeric five-field cron with *, ranges, lists and steps")
        next_tick(self, self.start_at - timedelta(microseconds=1))
        return self


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def next_tick(spec: ScheduleInput, after: datetime) -> datetime | None:
    """Strictly after `after`, anchored at inclusive start and before exclusive stop."""
    start, after = aware(spec.start_at), aware(after)
    if spec.kind == "interval":
        index = max(0, math.floor((after - start).total_seconds() / spec.interval_seconds) + 1)
        result = start + timedelta(seconds=index * spec.interval_seconds)
    else:
        base = max(after, start - timedelta(microseconds=1)).astimezone(ZoneInfo(spec.timezone))
        try:
            iterator = croniter(spec.cron, base, max_years_between_matches=8)
            for _ in range(4):
                candidate = iterator.get_next(datetime)
                result = candidate.astimezone(UTC)
                local = result.astimezone(ZoneInfo(spec.timezone))
                if result > after and croniter.match(spec.cron, local.replace(tzinfo=None)):
                    break
            else:
                raise ValueError("Cron could not resolve the next local occurrence")
        except CroniterBadDateError as exc:
            raise ValueError("Cron has no occurrence within eight years") from exc
    return None if spec.stop_at is not None and result >= aware(spec.stop_at) else result


class ScheduleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    definition_id: UUID
    configuration: ScheduleInput
    enabled: bool
    version: int
    execution_count: int
    next_at: datetime | None
    last_request_id: UUID | None
    last_tick_at: datetime | None
    last_result: str | None
    created_at: datetime
