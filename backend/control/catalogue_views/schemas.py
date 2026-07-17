from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from control.catalogue_materializations.schemas import CatalogueMaterializationSummary


class CatalogueViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    description: str | None = Field(default=None, max_length=2_000)
    sql: str = Field(min_length=1, max_length=100_000)
    created_from_query_revision_id: UUID | None = None


class CatalogueViewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_ducklake_view_uuid: UUID
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    description: str | None = Field(default=None, max_length=2_000)
    sql: str = Field(min_length=1, max_length=100_000)


class CatalogueViewAdopt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ducklake_view_uuid: UUID
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    description: str | None = Field(default=None, max_length=2_000)


class CatalogueViewRecord(BaseModel):
    id: UUID | None
    ducklake_view_uuid: UUID
    schema_name: str
    view_name: str
    qualified_name: str
    slug: str
    description: str | None
    fixture_path: str | None
    sql: str
    columns: list[str]
    column_types: list[str]
    managed: bool
    available: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_from_query_revision_id: UUID | None = None
    materialization: CatalogueMaterializationSummary | None = None


class CatalogueViewListResponse(BaseModel):
    items: list[CatalogueViewRecord]
