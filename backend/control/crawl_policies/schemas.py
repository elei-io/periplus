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
