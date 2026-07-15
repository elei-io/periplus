from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ViewMaterializationPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    scope_kind: Literal["document", "crawl"]
    scope_column: str = Field(min_length=1, max_length=255)
    backfill_scopes_per_minute: int = Field(default=60, ge=1, le=10_000)
    partition_column: str | None = Field(default=None, max_length=63)


class CatalogueMaterializationMaintenanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    live_enabled: bool | None = None
    backfill_enabled: bool | None = None
    backfill_scopes_per_minute: int | None = Field(default=None, ge=1, le=10_000)

    @model_validator(mode="after")
    def require_change(self) -> "CatalogueMaterializationMaintenanceUpdate":
        if not self.model_fields_set:
            raise ValueError("Choose at least one maintenance setting to change.")
        return self


class CatalogueMaterializationRebuild(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_ducklake_table_uuid: UUID


class CatalogueMaterializationColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool


class CatalogueMaterializationSummary(BaseModel):
    id: UUID
    status: Literal[
        "live", "backfilling", "paused", "dematerializing", "degraded",
        "source_changed",
    ]
    row_count: int
    storage_bytes: int
    definition_is_current: bool
    pending_live_scopes: int
    remaining_backfill_scopes: int
    failed_scopes: int
    last_scope_completed_at: datetime | None


class CatalogueMaterializationRecord(BaseModel):
    id: UUID
    name: str
    qualified_name: str
    display_name: str
    description: str | None
    view_reference_id: UUID
    view_uuid: UUID
    view_name: str
    scope_kind: Literal["document", "crawl"]
    scope_column: str
    activation_snapshot: int
    live_enabled: bool
    backfill_enabled: bool
    backfill_scopes_per_minute: int
    partition_column: str | None
    partitioning: list[str]
    status: Literal[
        "live", "backfilling", "paused", "dematerializing", "degraded",
        "source_changed",
    ]
    source_state: Literal["current", "source_changed"]
    completed_scopes: int | None
    total_scopes: int | None
    pending_live_scopes: int
    remaining_backfill_scopes: int
    failed_scopes: int
    last_scope_completed_at: datetime | None
    active_file_count: int
    active_storage_bytes: int
    dematerialization_requested_at: datetime | None
    ducklake_table_uuid: UUID
    row_count: int
    columns: list[CatalogueMaterializationColumn]
    last_refreshed_at: datetime
    created_at: datetime
    updated_at: datetime


class CatalogueMaterializationListResponse(BaseModel):
    items: list[CatalogueMaterializationRecord]
    total: int
