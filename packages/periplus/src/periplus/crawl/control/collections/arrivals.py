"""Bounded API records and pagination contracts."""

import base64
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field
from periplus.crawl.control.collections.schemas import CollectionExecutionSpec


class ArrivalCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    collection_id: UUID
    decided_at: datetime
    fulfillment_id: UUID


class CollectionArrival(BaseModel):
    fulfillment_id: UUID
    observation_id: UUID
    requested_url: str = Field(max_length=8192)
    parent_observation_id: UUID | None
    depth: int = Field(ge=0)
    rule_id: str = Field(max_length=200)
    mode: Literal["acquired", "shared", "reused"]
    decided_at: datetime
    observation_committed: bool
    effective_url: str | None = Field(max_length=8192)
    observed_at: datetime | None
    outcome: str | None
    http_status_code: int | None
    query_ready: bool | None = None
    query_readiness_reason: str = "materialization_commit_not_verified"


class CollectionArrivalsPage(BaseModel):
    source: Literal["history"] = "history"
    collection_id: UUID
    definition_committed: bool = True
    items: list[CollectionArrival]
    next_cursor: str | None
    as_of: datetime


def decode_arrival_cursor(value: str | None, identity: UUID) -> ArrivalCursor | None:
    if value is None:
        return None
    try:
        if len(value) > 512:
            raise ValueError("cursor too long")
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        cursor = ArrivalCursor.model_validate_json(raw)
        if cursor.collection_id != identity or cursor.decided_at.utcoffset() is None:
            raise ValueError("cursor identity or timezone mismatch")
        return cursor
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid collection arrival cursor") from exc
