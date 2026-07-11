from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MaterializedViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=63)
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    query_revision_id: UUID


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
    query_revision_id: UUID
    query_id: UUID
    query_name: str
    query_revision: int
    ducklake_table_uuid: UUID
    row_count: int
    columns: list[MaterializedViewColumn]
    last_refreshed_at: datetime
    created_at: datetime
    updated_at: datetime


class MaterializedViewListResponse(BaseModel):
    items: list[MaterializedViewRecord]
    total: int
