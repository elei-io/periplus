from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


MaterializationDesiredState = Literal["live", "paused", "deleting"]
MaterializationObservedState = Literal[
    "creating", "live", "paused", "deleting", "blocked_schema", "failed"
]


class ViewMaterializationPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    source_table: str = Field(min_length=1, max_length=63)
    refresh_delay_seconds: float = Field(default=1.0, ge=0, le=3600)
    partition_column: str | None = Field(default=None, max_length=63)


class CatalogueMaterializationStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desired_state: Literal["live", "paused"] | None = None
    refresh_delay_seconds: float | None = Field(default=None, ge=0, le=3600)

    @model_validator(mode="after")
    def require_change(self) -> "CatalogueMaterializationStateUpdate":
        if not self.model_fields_set:
            raise ValueError("Choose at least one setting to change.")
        return self


class CatalogueMaterializationSummary(BaseModel):
    id: UUID
    status: MaterializationObservedState


class CatalogueMaterializationRecord(BaseModel):
    id: UUID
    name: str
    qualified_name: str
    display_name: str
    description: str | None
    view_reference_id: UUID
    view_uuid: UUID
    view_name: str
    source_table: str
    source_table_id: int
    source_table_uuid: UUID
    control_snapshot: int
    desired_state: MaterializationDesiredState
    observed_state: MaterializationObservedState
    nats_consumer_name: str
    refresh_delay_seconds: float
    partition_column: str | None
    target_table_id: int | None
    ducklake_table_uuid: UUID | None
    bootstrap_snapshot: int | None
    processed_snapshot: int | None
    last_refreshed_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class CatalogueMaterializationListResponse(BaseModel):
    items: list[CatalogueMaterializationRecord]
    total: int
