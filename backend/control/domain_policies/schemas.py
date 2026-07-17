from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_host_match(value: str) -> str:
    host = value.strip().lower()
    if not host or "/" in host or "://" in host or "?" in host or "#" in host:
        raise ValueError("host_match must be * or a hostname")
    return host


class DomainPolicySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    slug: str
    host_match: str
    maximum_concurrency: int = Field(ge=1, le=10_000)
    minimum_request_interval_seconds: float = Field(ge=0, le=3600)


class DomainPolicyRecord(DomainPolicySnapshot):
    enabled: bool
    created_at: datetime
    updated_at: datetime


class DomainPolicyListResponse(BaseModel):
    items: list[DomainPolicyRecord]
    total: int
    limit: int
    offset: int


class DomainPolicyCreateRequest(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    host_match: str
    maximum_concurrency: int = Field(default=4, ge=1, le=10_000)
    minimum_request_interval_seconds: float = Field(default=0, ge=0, le=3600)
    enabled: bool = True

    @field_validator("host_match")
    @classmethod
    def normalize_host(cls, value: str) -> str:
        return normalize_host_match(value)


class DomainPolicyUpdateRequest(BaseModel):
    host_match: str | None = None
    maximum_concurrency: int | None = Field(default=None, ge=1, le=10_000)
    minimum_request_interval_seconds: float | None = Field(default=None, ge=0, le=3600)
    enabled: bool | None = None

    @field_validator("host_match")
    @classmethod
    def normalize_host(cls, value: str | None) -> str | None:
        return None if value is None else normalize_host_match(value)
