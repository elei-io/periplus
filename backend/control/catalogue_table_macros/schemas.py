from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CatalogueTableMacroCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    parameters: list[str] = Field(default_factory=list, max_length=32)
    sql: str = Field(min_length=1, max_length=100_000)
    created_from_query_revision_id: UUID | None = None


class CatalogueTableMacroUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_definition_revision_id: UUID
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    parameters: list[str] = Field(default_factory=list, max_length=32)
    sql: str = Field(min_length=1, max_length=100_000)


class CatalogueTableMacroRecord(BaseModel):
    id: UUID
    schema_name: str
    macro_name: str
    qualified_name: str
    display_name: str
    description: str | None
    parameters: list[str]
    sql: str
    definition_revision_id: UUID
    fixture_path: str | None
    available: bool
    created_from_query_revision_id: UUID | None
    created_at: datetime
    updated_at: datetime


class CatalogueTableMacroListResponse(BaseModel):
    items: list[CatalogueTableMacroRecord]
