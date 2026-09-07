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
    background_share: int = Field(default=0, ge=0, le=99)
    background_attempt_allowance: int = Field(default=1000, ge=0, le=1000000000)
    background_capture_time_allowance_ms: int = Field(default=12500000, ge=0, le=1000000000000)
    attempt_allowance: int = Field(default=10000, ge=0, le=1000000000)
    capture_time_allowance_ms: int = Field(default=86400000, ge=0, le=1000000000000)
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
    reserved_attempts: int
    started_attempts: int
    reserved_capture_ms: int
    charged_capture_ms: int
    background_reserved_attempts: int
    background_started_attempts: int
    background_reserved_capture_ms: int
    background_charged_capture_ms: int
    background_waiting_reason: str | None
    background_share_semantics: Literal["percent_when_both_eligible_spare_capacity_otherwise"] = "percent_when_both_eligible_spare_capacity_otherwise"
    dispatch_waiting_reason: str | None
    allowance_semantics: Literal["cumulative_until_operator_increases_limit"] = "cumulative_until_operator_increases_limit"
    time_semantics: Literal["client_capture_elapsed_not_provider_billing"] = "client_capture_elapsed_not_provider_billing"
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
