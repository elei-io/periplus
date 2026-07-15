from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from actions.shared.cache import CacheOptions

CrawlTransport = Literal["http", "browser", "firecrawl"]
PathMode = Literal["exact", "prefix"]
DEFAULT_HTTP_USER_AGENT = (
    "AtlasBot/0.1.0 (https://github.com/ekkuleivonen/atlas)"
)

_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_FORBIDDEN_HTTP_PROFILE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "user-agent",
    }
)
_GENERIC_HTTP_USER_AGENT_PREFIXES = (
    "curl/",
    "httpx/",
    "python-httpx/",
    "python-requests/",
    "python-urllib/",
    "wget/",
)


def _normalize_match_host(value: str) -> str:
    host = value.strip().lower()
    if not host or "/" in host or "://" in host or "?" in host or "#" in host:
        raise ValueError("host must be * or a hostname with an optional port")
    return host


def _normalize_match_path(value: str) -> str:
    path = value.strip()
    if not path.startswith("/") or "?" in path or "#" in path:
        raise ValueError("path_prefix must begin with / and contain no query or fragment")
    return path


class ProfileConfig(BaseModel):
    """Settings shared by every acquisition transport."""

    model_config = ConfigDict(extra="allow")

    cache: CacheOptions | None = None
    cache_block_rules: dict[str, JsonValue] = Field(default_factory=dict)
    artifact_media_types: tuple[
        Literal["application/pdf", "image/*", "video/*"], ...
    ] = ()
    artifact_max_bytes: int = Field(
        default=64 * 1024 * 1024, ge=1, le=256 * 1024 * 1024
    )

    @field_validator("artifact_media_types")
    @classmethod
    def unique_artifact_media_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("artifact media types must be unique")
        return value


class HttpProfileConfig(ProfileConfig):
    timeout_seconds: float = Field(default=20, gt=0)
    user_agent: str = Field(min_length=16, max_length=512)
    headers: dict[str, str] = Field(default_factory=dict, max_length=32)
    follow_redirects: bool = True

    @field_validator("user_agent")
    @classmethod
    def descriptive_user_agent(cls, value: str) -> str:
        user_agent = value.strip()
        if user_agent.lower().startswith(_GENERIC_HTTP_USER_AGENT_PREFIXES):
            raise ValueError("user agent must identify the crawler, not its HTTP library")
        if "\r" in user_agent or "\n" in user_agent:
            raise ValueError("user agent must not contain line breaks")
        if "(" not in user_agent or ")" not in user_agent:
            raise ValueError("user agent must include operator contact in parentheses")
        return user_agent

    @field_validator("headers")
    @classmethod
    def safe_headers(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_name, raw_value in value.items():
            name = raw_name.strip()
            if not _HTTP_HEADER_NAME.fullmatch(name):
                raise ValueError(f"invalid HTTP header name: {raw_name!r}")
            if name.lower() in _FORBIDDEN_HTTP_PROFILE_HEADERS:
                raise ValueError(f"HTTP profile header {name!r} is not allowed")
            header_value = raw_value.strip()
            if not header_value or "\r" in header_value or "\n" in header_value:
                raise ValueError(f"HTTP profile header {name!r} has an invalid value")
            if len(header_value) > 4096:
                raise ValueError(f"HTTP profile header {name!r} is too long")
            normalized[name] = header_value
        return normalized


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


def parse_profile_config(transport: CrawlTransport, config: dict[str, Any]) -> ProfileConfig:
    parsed = PROFILE_CONFIG_TYPES[transport].model_validate(config)
    if transport != "http" and parsed.artifact_media_types:
        raise ValueError("artifact capture is supported only by the HTTP transport")
    return parsed


class CrawlProfileSnapshot(BaseModel):
    """Complete acquisition behaviour frozen into queued work."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1)
    transport: CrawlTransport
    config: dict[str, Any] = Field(default_factory=dict)
    cost_rank: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_transport_config(self) -> CrawlProfileSnapshot:
        parse_profile_config(self.transport, self.config)
        return self

    def parsed_config(self) -> ProfileConfig:
        return parse_profile_config(self.transport, self.config)


class CrawlPolicySnapshot(BaseModel):
    """Complete policy resolution frozen when crawl work is queued."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    origin: Literal["editable", "system_trial"] = "editable"
    slug: str
    scheme: Literal["*", "http", "https"]
    host: str
    path_prefix: str
    path_mode: PathMode
    max_concurrency: int = Field(ge=1)
    profile: CrawlProfileSnapshot
    trial_candidate: CrawlProfileSnapshot | None = None


class CrawlProfileRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    description: str | None
    transport: CrawlTransport
    config: dict[str, Any]
    cost_rank: int
    trial_eligible: bool
    created_at: datetime
    updated_at: datetime


class CrawlProfileListResponse(BaseModel):
    items: list[CrawlProfileRecord]
    total: int
    limit: int
    offset: int


class CrawlProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    config: dict[str, Any] | None = None
    cost_rank: int | None = Field(default=None, ge=0)
    trial_eligible: bool | None = None


class CrawlProfileCreateRequest(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1)
    description: str | None = None
    transport: CrawlTransport
    config: dict[str, Any] = Field(default_factory=dict)
    cost_rank: int = Field(ge=0)
    trial_eligible: bool = False

    @model_validator(mode="after")
    def validate_transport_config(self) -> CrawlProfileCreateRequest:
        parse_profile_config(self.transport, self.config)
        return self


class CrawlPolicyRecord(BaseModel):
    id: UUID
    slug: str
    scheme: Literal["*", "http", "https"]
    host: str
    path_prefix: str
    path_mode: PathMode
    match: str
    profile: CrawlProfileRecord
    max_concurrency: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class CrawlPolicyListResponse(BaseModel):
    items: list[CrawlPolicyRecord]
    total: int
    limit: int
    offset: int


class PolicyTrialComparisonRecord(BaseModel):
    scheme: Literal["http", "https"]
    host: str
    port: int
    registrable_domain: str
    use_profile: str
    candidate_profile: str
    use_profile_config_hash: str
    candidate_profile_config_hash: str
    candidate_profile_definition_hash: str
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
    current_profile: str
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
    profile: str = Field(min_length=1)


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
    scheme: Literal["*", "http", "https"] | None = None
    host: str | None = Field(default=None, min_length=1)
    path_prefix: str | None = Field(default=None, min_length=1)
    path_mode: PathMode | None = None
    profile_id: UUID | None = None
    max_concurrency: int | None = Field(default=None, ge=1)

    @field_validator("host")
    @classmethod
    def normalize_host(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_match_host(value)

    @field_validator("path_prefix")
    @classmethod
    def normalize_path(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_match_path(value)


class CrawlPolicyCreateRequest(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    scheme: Literal["*", "http", "https"]
    host: str = Field(min_length=1)
    path_prefix: str = Field(default="/", min_length=1)
    path_mode: PathMode = "prefix"
    profile_id: UUID
    max_concurrency: int = Field(default=4, ge=1)
    enabled: bool = True

    @field_validator("host")
    @classmethod
    def normalize_host(cls, value: str) -> str:
        return _normalize_match_host(value)

    @field_validator("path_prefix")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        return _normalize_match_path(value)
