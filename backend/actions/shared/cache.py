"""Typed cache-control semantics shared by every page-acquiring action."""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CacheMode = Literal["prefer", "refresh", "no_store"]


class CacheOptions(BaseModel):
    """Optional request or CrawlPolicy overrides for acquisition caching."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: CacheMode | None = None
    max_age_seconds: int | None = Field(default=None, ge=0)
    stale_if_error_seconds: int | None = Field(default=None, ge=0)


class ResolvedCachePolicy(BaseModel):
    """Complete cache policy frozen into one logical acquisition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: CacheMode
    max_age_seconds: int = Field(ge=0)
    stale_if_error_seconds: int | None = Field(default=None, ge=0)

    @property
    def reads_cache(self) -> bool:
        return self.mode == "prefer"

    @property
    def stores_result(self) -> bool:
        return self.mode != "no_store"


def resolve_cache_policy(
    *,
    crawl_policy_config: dict[str, Any] | None,
    request: CacheOptions | dict[str, Any] | None,
) -> ResolvedCachePolicy:
    """Resolve request overrides over CrawlPolicy defaults and Atlas defaults."""

    policy_options = _options_from_policy(crawl_policy_config)
    request_options = (
        request if isinstance(request, CacheOptions) else CacheOptions.model_validate(request or {})
    )
    mode = request_options.mode or policy_options.mode or "prefer"
    max_age_seconds = _first_not_none(
        request_options.max_age_seconds,
        policy_options.max_age_seconds,
        _env_nonnegative_int("ATLAS_CACHE_MAX_AGE_SECONDS", default=120),
    )
    stale_if_error_seconds = _first_not_none(
        request_options.stale_if_error_seconds,
        policy_options.stale_if_error_seconds,
        _env_optional_nonnegative_int("ATLAS_CACHE_STALE_IF_ERROR_SECONDS"),
    )
    if (
        stale_if_error_seconds is not None
        and stale_if_error_seconds < max_age_seconds
    ):
        raise ValueError(
            "stale_if_error_seconds must be greater than or equal to max_age_seconds"
        )
    return ResolvedCachePolicy(
        mode=mode,
        max_age_seconds=max_age_seconds,
        stale_if_error_seconds=stale_if_error_seconds,
    )


def _options_from_policy(config: dict[str, Any] | None) -> CacheOptions:
    raw = (config or {}).get("cache")
    if raw is None:
        return CacheOptions()
    if not isinstance(raw, dict):
        raise ValueError("CrawlPolicy cache configuration must be an object")
    return CacheOptions.model_validate(raw)


def _first_not_none(*values: int | None) -> int | None:
    return next((value for value in values if value is not None), None)


def _env_nonnegative_int(name: str, *, default: int) -> int:
    value = _env_optional_nonnegative_int(name)
    return default if value is None else value


def _env_optional_nonnegative_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be zero or greater")
    return value
