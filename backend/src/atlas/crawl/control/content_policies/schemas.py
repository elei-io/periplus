from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas.crawl.control.domain_policies.schemas import DomainPolicySnapshot

PathMode = Literal["exact", "prefix"]
ResponseOutcome = Literal["retry", "fail", "skip", "accept"]
ContentVarianceArm = Literal["lower", "baseline", "higher"]
ContentVarianceSetting = Literal[
    "wait_dynamic.maximum_wait_ms",
    "wait_dynamic.stable_samples",
    "wait_fixed.duration_ms",
    "scroll.maximum_iterations",
    "scroll.wait_ms",
    "scroll.stable_bottom_samples",
    "expand.maximum_actions",
    "expand.wait_ms",
]


def _normalize_match_host(value: str) -> str:
    host = value.strip().lower()
    if not host or "/" in host or "://" in host or "?" in host or "#" in host:
        raise ValueError("host must be * or a hostname with an optional port")
    if "*" in host and not (
        host.startswith("*.") and host.count("*") == 1 and len(host) > 2
    ) and host != "*":
        raise ValueError(
            "host must be *, an exact hostname, or a *.domain wildcard"
        )
    return host


def _normalize_match_path(value: str) -> str:
    path = value.strip()
    if not path.startswith("/") or "?" in path or "#" in path:
        raise ValueError("path_prefix must begin with / and contain no query or fragment")
    return path


class HttpStatusRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum: int = Field(ge=100, le=599)
    maximum: int = Field(ge=100, le=599)
    outcome: ResponseOutcome

    @model_validator(mode="after")
    def ordered_range(self) -> HttpStatusRule:
        if self.maximum < self.minimum:
            raise ValueError("HTTP status rule maximum must be at least its minimum")
        return self

    def matches(self, status: int) -> bool:
        return self.minimum <= status <= self.maximum


def _default_http_status_rules() -> tuple[HttpStatusRule, ...]:
    return (
        HttpStatusRule(minimum=408, maximum=408, outcome="retry"),
        HttpStatusRule(minimum=425, maximum=425, outcome="retry"),
        HttpStatusRule(minimum=429, maximum=429, outcome="retry"),
        HttpStatusRule(minimum=500, maximum=599, outcome="retry"),
        HttpStatusRule(minimum=400, maximum=499, outcome="fail"),
    )


class ResponseRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    http_status: tuple[HttpStatusRule, ...] = Field(default_factory=_default_http_status_rules)
    unsupported_content_type: ResponseOutcome = "skip"


class WaitDynamicCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    maximum_wait_ms: int = Field(default=8_000, ge=1, le=120_000)
    sample_interval_ms: int = Field(default=250, ge=10, le=10_000)
    stable_samples: int = Field(default=3, ge=1, le=100)


class WaitFixedCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    duration_ms: int = Field(default=0, ge=0, le=120_000)

    @model_validator(mode="after")
    def enabled_duration(self) -> WaitFixedCompletion:
        if self.enabled and self.duration_ms == 0:
            raise ValueError("enabled fixed wait requires duration_ms greater than zero")
        return self


class ScrollCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    maximum_iterations: int = Field(default=30, ge=1, le=1_000)
    viewport_ratio: float = Field(default=0.85, gt=0, le=1)
    wait_ms: int = Field(default=250, ge=0, le=30_000)
    stable_bottom_samples: int = Field(default=3, ge=1, le=100)


class ExpandCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    maximum_actions: int = Field(default=10, ge=1, le=1_000)
    wait_ms: int = Field(default=500, ge=0, le=30_000)


class NavigationCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout_ms: int = Field(default=30_000, ge=1_000, le=120_000)
    context_replacement_retries: int = Field(default=2, ge=0, le=10)
    context_replacement_settle_ms: int = Field(default=1_000, ge=0, le=30_000)


class ContentCompletion(BaseModel):
    """Four code-owned page-completion capabilities with policy-owned budgets."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    navigation: NavigationCompletion = Field(default_factory=NavigationCompletion)
    wait_dynamic: WaitDynamicCompletion = Field(default_factory=WaitDynamicCompletion)
    wait_fixed: WaitFixedCompletion = Field(default_factory=WaitFixedCompletion)
    scroll: ScrollCompletion = Field(default_factory=ScrollCompletion)
    expand: ExpandCompletion = Field(default_factory=ExpandCompletion)

    @property
    def browser_interaction_enabled(self) -> bool:
        return any(
            method.enabled
            for method in (
                self.wait_dynamic,
                self.wait_fixed,
                self.scroll,
                self.expand,
            )
        )


class ContentPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted_content_types: tuple[str, ...] = ("text/html", "application/xhtml+xml")
    response_rules: ResponseRules = Field(default_factory=ResponseRules)
    completion: ContentCompletion = Field(default_factory=ContentCompletion)

    @field_validator("accepted_content_types")
    @classmethod
    def normalize_content_types(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().lower() for value in values if value.strip())
        if not normalized:
            raise ValueError("accepted_content_types must contain at least one media type")
        if len(normalized) != len(set(normalized)):
            raise ValueError("accepted_content_types must be unique")
        return normalized


class ContentPolicyVariance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    setting: ContentVarianceSetting
    arm: ContentVarianceArm
    configured_value: int
    effective_value: int


class ContentPolicySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    slug: str
    scheme: Literal["*", "http", "https"]
    host: str
    path_prefix: str
    path_mode: PathMode
    content: ContentPolicyConfig = Field(default_factory=ContentPolicyConfig)
    content_variance: ContentPolicyVariance | None = None


class EffectivePolicySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: ContentPolicySnapshot
    domain: DomainPolicySnapshot


class ContentPolicyRecord(BaseModel):
    id: UUID
    slug: str
    scheme: Literal["*", "http", "https"]
    host: str
    path_prefix: str
    path_mode: PathMode
    match: str
    content: ContentPolicyConfig
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ContentPolicyListResponse(BaseModel):
    items: list[ContentPolicyRecord]
    total: int
    limit: int
    offset: int


class _PolicyFields(BaseModel):
    @field_validator("host", check_fields=False)
    @classmethod
    def normalize_host(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_match_host(value)

    @field_validator("path_prefix", check_fields=False)
    @classmethod
    def normalize_path(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_match_path(value)


class ContentPolicyUpdateRequest(_PolicyFields):
    enabled: bool | None = None
    scheme: Literal["*", "http", "https"] | None = None
    host: str | None = Field(default=None, min_length=1)
    path_prefix: str | None = Field(default=None, min_length=1)
    path_mode: PathMode | None = None
    content: ContentPolicyConfig | None = None


class ContentPolicyCreateRequest(_PolicyFields):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    scheme: Literal["*", "http", "https"]
    host: str = Field(min_length=1)
    path_prefix: str = Field(default="/", min_length=1)
    path_mode: PathMode = "prefix"
    content: ContentPolicyConfig = Field(default_factory=ContentPolicyConfig)
    enabled: bool = True
