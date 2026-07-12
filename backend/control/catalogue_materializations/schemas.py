from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CatalogueMaterializationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    refresh_mode: Literal["full", "scope_incremental"] = "full"
    scope_kind: Literal["document", "crawl"] | None = None
    scope_column: str | None = Field(default=None, max_length=255)
    live_enabled: bool = False
    backfill_enabled: bool = False
    backfill_scopes_per_minute: int = Field(default=60, ge=1, le=10_000)
    partition_column: str | None = Field(default=None, max_length=63)

    @model_validator(mode="after")
    def validate_mode(self) -> "CatalogueMaterializationCreate":
        if self.refresh_mode == "scope_incremental":
            if self.scope_kind is None or not self.scope_column:
                raise ValueError(
                    "Incremental materialization requires a scope kind and column."
                )
        elif self.live_enabled or self.backfill_enabled:
            raise ValueError(
                "Only incremental materializations can enable live work or backfill."
            )
        return self


class QueryMaterializationPut(CatalogueMaterializationCreate):
    active_query_revision_id: UUID | None = None


class ViewMaterializationPut(CatalogueMaterializationCreate):
    pass


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
    target_query_revision_id: UUID | None = None


class CatalogueMaterializationColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool


class CatalogueMaterializationSummary(BaseModel):
    id: UUID
    status: Literal[
        "full_refresh", "live", "backfilling", "paused", "dematerializing", "degraded",
        "source_changing", "source_changed",
    ]
    refresh_mode: Literal["full", "scope_incremental"]
    row_count: int
    storage_bytes: int
    active_query_revision: int | None
    definition_is_current: bool


class CatalogueMaterializationRecord(BaseModel):
    id: UUID
    name: str
    qualified_name: str
    display_name: str
    description: str | None
    active_query_revision_id: UUID | None
    query_id: UUID | None
    query_name: str | None
    query_revision: int | None
    view_reference_id: UUID | None
    view_uuid: UUID | None
    view_name: str | None
    refresh_mode: str
    scope_kind: str | None
    activation_snapshot: int | None
    live_enabled: bool
    backfill_enabled: bool
    backfill_scopes_per_minute: int
    partition_column: str | None
    partitioning: list[str]
    status: Literal[
        "full_refresh", "live", "backfilling", "paused", "dematerializing", "degraded",
        "source_changing", "source_changed",
    ]
    source_state: Literal["current", "source_changing", "source_changed"]
    completed_scopes: int | None
    total_scopes: int | None
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
