from datetime import datetime
from uuid import UUID

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MaterializedViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=63)
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    query_revision_id: UUID | None = None
    source_view_uuid: UUID | None = None
    refresh_mode: Literal["full", "scope_incremental"] = "full"
    scope_kind: Literal["document"] | None = None
    scope_column: str | None = Field(default=None, max_length=63)
    live_enabled: bool = False
    backfill_enabled: bool = False
    backfill_scopes_per_minute: int = Field(default=60, ge=1, le=10_000)
    partition_column: str | None = Field(default=None, max_length=63)

    @model_validator(mode="after")
    def validate_source_and_mode(self) -> "MaterializedViewCreate":
        if (self.query_revision_id is None) == (self.source_view_uuid is None):
            raise ValueError("Choose exactly one saved-query revision or view source.")
        if self.refresh_mode == "scope_incremental":
            if self.query_revision_id is None:
                raise ValueError("Incremental materialization requires a saved-query revision.")
            if self.scope_kind is None or not self.scope_column:
                raise ValueError("Incremental materialization requires a scope kind and column.")
        elif self.live_enabled or self.backfill_enabled:
            raise ValueError("Only incremental materializations can enable live work or backfill.")
        return self


class MaterializedViewMaintenanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    live_enabled: bool | None = None
    backfill_enabled: bool | None = None
    backfill_scopes_per_minute: int | None = Field(default=None, ge=1, le=10_000)

    @model_validator(mode="after")
    def require_change(self) -> "MaterializedViewMaintenanceUpdate":
        if not self.model_fields_set:
            raise ValueError("Choose at least one maintenance setting to change.")
        return self


class MaterializedViewRefresh(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_ducklake_table_uuid: UUID


class MaterializedViewColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool


class MaterializedViewRecord(BaseModel):
    id: UUID
    name: str
    qualified_name: str
    display_name: str
    description: str | None
    query_revision_id: UUID | None
    query_id: UUID | None
    query_name: str | None
    query_revision: int | None
    source_view_uuid: UUID | None
    source_view_name: str | None
    refresh_mode: str
    scope_kind: str | None
    activation_snapshot: int | None
    live_enabled: bool
    backfill_enabled: bool
    backfill_scopes_per_minute: int
    partition_column: str | None
    partitioning: list[str]
    status: Literal[
        "full_refresh", "live", "backfilling", "paused", "deleting", "degraded"
    ]
    completed_scopes: int | None
    total_scopes: int | None
    failed_scopes: int
    last_scope_completed_at: datetime | None
    active_file_count: int
    active_storage_bytes: int
    deletion_requested_at: datetime | None
    ducklake_table_uuid: UUID
    row_count: int
    columns: list[MaterializedViewColumn]
    last_refreshed_at: datetime
    created_at: datetime
    updated_at: datetime


class MaterializedViewListResponse(BaseModel):
    items: list[MaterializedViewRecord]
    total: int
