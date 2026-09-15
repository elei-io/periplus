"""Bounded API records and pagination contracts."""

import base64
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field
from periplus.crawl.control.collections.schemas import CollectionExecutionSpec


class LineageCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    observation_id: UUID
    decided_at: AwareDatetime
    record_id: UUID
    kind: Literal["fulfillment", "reason"]


class ObservationLineageItem(BaseModel):
    record_id: UUID
    kind: Literal["fulfillment", "reason"]
    decided_at: datetime
    collection_id: UUID
    parent_observation_id: UUID | None
    rule_id: str = Field(max_length=200)
    depth: int | None = Field(ge=0)
    mode: Literal["acquired", "shared", "reused"] | None
    reason: Literal["collection"] | None
    policy_version: str | None = Field(max_length=200)


class ObservationLineagePage(BaseModel):
    observation_id: UUID
    requested_url: str = Field(max_length=8192)
    items: list[ObservationLineageItem]
    next_cursor: str | None
    as_of: datetime
    completeness: Literal["committed_visible_evidence_only_ingestion_may_lag"] = (
        "committed_visible_evidence_only_ingestion_may_lag"
    )


def decode_lineage_cursor(value: str | None, identity: UUID):
    if value is None:
        return None
    try:
        if len(value) > 512:
            raise ValueError("cursor too long")
        cursor = LineageCursor.model_validate_json(
            base64.b64decode(
                value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
            )
        )
        if cursor.observation_id != identity:
            raise ValueError("cursor belongs to another observation")
        return cursor
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid observation lineage cursor") from exc
