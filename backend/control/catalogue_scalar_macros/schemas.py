from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CatalogueScalarMacroCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    description: str | None = Field(default=None, max_length=2_000)
    parameters: list[str] = Field(default_factory=list, max_length=32)
    sql: str = Field(min_length=1, max_length=100_000)


class CatalogueScalarMacroUpdate(CatalogueScalarMacroCreate):
    expected_definition_revision_id: UUID


class CatalogueScalarMacroRecord(BaseModel):
    id: UUID
    kind: str = "scalar"
    schema_name: str
    macro_name: str
    qualified_name: str
    slug: str
    description: str | None
    parameters: list[str]
    sql: str
    definition_revision_id: UUID
    fixture_path: str | None
    available: bool
    created_at: datetime
    updated_at: datetime


class CatalogueScalarMacroListResponse(BaseModel):
    items: list[CatalogueScalarMacroRecord]
