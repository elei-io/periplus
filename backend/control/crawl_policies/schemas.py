from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from actions.shared.cache import CacheOptions


class ProfileConfig(BaseModel):
    """Settings shared by every acquisition profile."""

    model_config = ConfigDict(extra="allow")

    cache: CacheOptions | None = None
    cache_block_rules: dict[str, JsonValue] = Field(default_factory=dict)


class HttpProfileConfig(ProfileConfig):
    timeout_seconds: float = Field(default=20, gt=0)
    headers: dict[str, str] = Field(default_factory=dict)
    follow_redirects: bool = True


class BrowserProfileConfig(ProfileConfig):
    mode: Literal["static", "dynamic", "app"] = "static"
    wait: Literal["none", "stable", "network", "fixed"] = "none"
    run_config_overrides: dict[str, Any] = Field(default_factory=dict)


class FirecrawlProfileConfig(ProfileConfig):
    timeout_seconds: float = Field(default=90, gt=0, le=300)
    api_url: str = "https://api.firecrawl.dev"
    provider_options: dict[str, JsonValue] = Field(default_factory=dict)


PROFILE_CONFIG_TYPES = {
    "http": HttpProfileConfig,
    "browser": BrowserProfileConfig,
    "firecrawl": FirecrawlProfileConfig,
}


class CrawlPolicyConfig(BaseModel):
    """Unified acquisition profile envelope frozen into crawl work."""

    model_config = ConfigDict(extra="forbid")

    profile: Literal["http", "browser", "firecrawl"] = "http"
    concurrency: int = Field(default=4, ge=1)
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_profile_config(self) -> CrawlPolicyConfig:
        PROFILE_CONFIG_TYPES[self.profile].model_validate(self.config)
        return self

    def parsed_config(self) -> ProfileConfig:
        return PROFILE_CONFIG_TYPES[self.profile].model_validate(self.config)


class UrlMatchSnapshot(BaseModel):
    """The immutable URL-matching fields used by a queued task run."""

    model_config = ConfigDict(frozen=True)

    scheme: str
    host: str
    path_pattern: str
    match_type: Literal["exact", "glob"]
    priority: int


class CrawlPolicySnapshot(BaseModel):
    """The complete CrawlPolicy execution view frozen when a run is queued."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    revision: int
    origin: Literal["editable", "system_trial"] = "editable"
    metric_slug: str
    domain_group: str
    match: str
    config: dict[str, Any]
    matcher: UrlMatchSnapshot


class CrawlPolicyRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    metric_slug: str
    domain_group: str
    url_match_id: UUID | None = None
    match: str
    enabled: bool
    config: dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime


class CrawlPolicyListRecord(CrawlPolicyRecord):
    template: str | None = None
    profile: str | None = None
    mode: str | None = None
    wait: str | None = None
    concurrency: int | None = None


class CrawlPolicyListResponse(BaseModel):
    items: list[CrawlPolicyListRecord]
    total: int
    limit: int
    offset: int


class PolicyTrialComparisonRecord(BaseModel):
    scheme: Literal["http", "https"]
    host: str
    port: int
    registrable_domain: str
    use_template: str
    candidate_template: str
    selected_trials: int
    completed_pairs: int
    recovered_crawls: int
    sample_failures: int
    identical_documents: int
    median_html_delta_percent: float | None
    median_visible_text_delta_percent: float | None
    median_element_delta_percent: float | None
    mean_use_visible_text_chars: float | None
    mean_sample_visible_text_chars: float | None
    use_visible_text_stddev: float | None
    sample_visible_text_stddev: float | None
    use_visible_text_cv: float | None
    sample_visible_text_cv: float | None
    use_distinct_document_ratio: float | None
    sample_distinct_document_ratio: float | None
    median_use_quality_flag_count: float | None
    median_sample_quality_flag_count: float | None
    use_acquisition_failure_count: int
    sample_acquisition_failure_count: int
    median_duration_delta_ms: float | None
    last_trial_at: datetime
    current_policy_id: UUID | None
    current_template: str
    applied: bool
    verdict: Literal[
        "awaiting_sample",
        "insufficient_evidence",
        "promising",
        "no_clear_gain",
        "regressed",
        "inconclusive",
    ]
    verdict_reason: str


class PolicyTrialApplyRequest(BaseModel):
    scheme: Literal["http", "https"]
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    template: str = Field(min_length=1)


class PolicyTrialSummaryRecord(BaseModel):
    sampling_active: bool
    configured_sample_rate: float
    observed_sample_rate: float
    max_in_flight: int
    use_crawls: int
    selected_trials: int
    sample_crawls: int
    completed_pairs: int
    awaiting_samples: int
    pairs_with_failure: int
    last_trial_at: datetime | None


class PolicyTrialReportResponse(BaseModel):
    summary: PolicyTrialSummaryRecord
    items: list[PolicyTrialComparisonRecord]
    total: int
    limit: int
    offset: int


class CrawlPolicyUpdateRequest(BaseModel):
    enabled: bool | None = None
    match: str | None = None
    config: dict[str, Any] | None = None
    domain_group: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")

    @field_validator("config")
    @classmethod
    def validate_cache_config(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            CrawlPolicyConfig.model_validate(value)
        return value
