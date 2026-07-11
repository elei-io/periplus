from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CatalogueQueryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    sql: str = Field(min_length=1, max_length=100_000)
    change_note: str | None = Field(default=None, max_length=500)


class CatalogueQueryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_current_revision_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    sql: str = Field(min_length=1, max_length=100_000)
    change_note: str | None = Field(default=None, max_length=500)


class CatalogueQueryRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_current_revision_id: UUID
    change_note: str | None = Field(default=None, max_length=500)


class CatalogueQueryRevisionRecord(BaseModel):
    id: UUID
    query_id: UUID
    revision: int
    sql: str
    sql_hash: str
    change_note: str | None
    created_at: datetime


class CatalogueQueryRecord(BaseModel):
    id: UUID
    name: str
    description: str | None
    current_revision_id: UUID
    current_revision: int
    sql: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CatalogueQueryDetail(CatalogueQueryRecord):
    revisions: list[CatalogueQueryRevisionRecord]


class CatalogueQueryListResponse(BaseModel):
    items: list[CatalogueQueryRecord]
    total: int
