"""Operator controls for the single crawler, separate from capture requirements."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


from periplus.crawl.control.collections.exclusions import UrlExclusion


class FrontierSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    paused: bool = False
    exclusions: tuple[UrlExclusion, ...] = Field(default=(), max_length=100)
    collection_limit: int = Field(default=1000, ge=1, le=100000)
    interest_limit: int = Field(default=200000, ge=1, le=10000000)
    acquisition_limit: int = Field(default=10000, ge=1, le=1000000)
    admission_limit: int = Field(default=10000, ge=1, le=1000000)
    dispatch_limit: int = Field(default=48, ge=1, le=10000)
    captures_per_minute: int | None = Field(default=60, ge=1, le=60000)
    capture_timeout_ms: int = Field(default=120000, ge=1000, le=3600000)


class ReplaceFrontierSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_version: int = Field(ge=1)
    settings: FrontierSettings


class FrontierControlView(BaseModel):
    settings: FrontierSettings
    policy_version: int
    updated_at: datetime | None
    updated_by: str | None
    retained_acquisitions: int
    acquisition_admission_waiting_reason: str | None
    pending_acquisitions: int
    dispatched_acquisitions: int
    retained_interests: int
    dispatch_waiting_reason: str | None
    next_rate_eligibility_at: datetime | None
    pause_behavior: Literal["finish_started_captures"] = "finish_started_captures"
    rate_semantics: Literal["dispatch_upper_bound_null_is_unlimited"] = "dispatch_upper_bound_null_is_unlimited"
    as_of: datetime


class ControlVersionConflict(ValueError):
    pass


def ensure_frontier_control(session) -> None:
    """Setup installs the singleton once; repeated setup preserves operator controls."""
    from periplus.crawl.runtime.frontier_models import FrontierControlRecord
    if session.get(FrontierControlRecord, 1) is None:
        session.add(FrontierControlRecord(id=1))
        session.flush()
