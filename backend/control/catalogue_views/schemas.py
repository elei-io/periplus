from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CatalogueViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    sql: str = Field(min_length=1, max_length=100_000)


class CatalogueViewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_ducklake_view_uuid: UUID
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    sql: str = Field(min_length=1, max_length=100_000)


class CatalogueViewAdopt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ducklake_view_uuid: UUID
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)


class CatalogueViewRecord(BaseModel):
    id: UUID | None
    ducklake_view_uuid: UUID
    schema_name: str
    view_name: str
    qualified_name: str
    display_name: str
    description: str | None
    sql: str
    columns: list[str]
    managed: bool
    available: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CatalogueViewListResponse(BaseModel):
    items: list[CatalogueViewRecord]
