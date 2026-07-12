from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from actions.shared.cache import CacheOptions


class CrawlPolicyConfig(BaseModel):
    """Currently executable acquisition settings frozen into crawl work."""

    model_config = ConfigDict(extra="allow")
    engine: Literal["crawl4ai"] = "crawl4ai"
    mode: Literal["static", "dynamic", "app"] = "static"
    wait: Literal["none", "stable", "network", "fixed"] = "none"
    run_config_overrides: dict[str, Any] = Field(default_factory=dict)
    max_concurrency: int | None = Field(default=None, ge=1)
    cache: CacheOptions | None = None
    cache_block_rules: dict[str, Any] = Field(default_factory=dict)


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
    mode: str | None = None
    wait: str | None = None
    max_concurrency: int | None = None


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
