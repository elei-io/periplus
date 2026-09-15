"""Bounded API records and pagination contracts."""

import base64
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field
from periplus.crawl.control.collections.schemas import CollectionExecutionSpec


class HistoricalCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["history"] = "history"
    id: UUID
    specification: CollectionExecutionSpec
    created_at: datetime
    completed_at: datetime | None
    outcome: str | None
    consumed_pages: int | None = Field(ge=0)
    supplied_pages: int | None = Field(ge=0)
    failed_pages: int | None = Field(ge=0)
    seed_provenance: dict | None
    as_of: datetime
    query_ready: bool | None = None
    query_readiness_reason: str = "materialization_commit_not_verified"
    query_readiness_as_of: datetime | None = None
    query_generation_id: UUID | None = None

    @computed_field
    @property
    def expires_at(self) -> datetime | None:
        from periplus.retention.policy import expires_at

        return expires_at(self.specification.retention_seconds, self.completed_at)

    @computed_field
    @property
    def retention_expired(self) -> bool:
        expiry = self.expires_at
        return expiry is not None and expiry <= self.as_of


class HistoryUnavailable(RuntimeError):
    pass


class HistoryCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requested_at: datetime
    id: UUID


class HistoricalCollectionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    request_class: Literal["public", "system", "admin"]
    summary: str = Field(max_length=500)
    created_at: datetime
    completed_at: datetime | None
    outcome: str | None
    consumed_pages: int | None = Field(ge=0)
    supplied_pages: int | None = Field(ge=0)
    failed_pages: int | None = Field(ge=0)


class CollectionHistoryPage(BaseModel):
    source: Literal["history"] = "history"
    items: list[HistoricalCollectionSummary]
    next_cursor: str | None
    as_of: datetime


def decode_cursor(value: str | None) -> HistoryCursor | None:
    if value is None:
        return None
    try:
        if len(value) > 512:
            raise ValueError("cursor too long")
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        cursor = HistoryCursor.model_validate_json(decoded)
        if cursor.requested_at.utcoffset() is None:
            raise ValueError("cursor time requires a timezone")
        return cursor
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid collection history cursor") from exc
