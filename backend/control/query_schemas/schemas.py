from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QuerySchemaRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    identity_key: str
    url_match_id: UUID | None = None
    match: str
    enabled: bool
    priority: int
    schema_type: str
    domain: str | None = None
    path: str | None = None
    extraction_schema: dict[str, Any] = Field(alias="schema_json")
    params_json: list[dict[str, Any]]
    evidence_json: list[dict[str, Any]]
    schema_hash: str
    generated_from_crawl_id: UUID | None = None
    generated_from_document_id: str | None = None
    inputs_json: dict[str, Any]
    warnings_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class QuerySchemaListRecord(BaseModel):
    id: UUID
    url_match_id: UUID | None = None
    match: str
    enabled: bool
    priority: int
    schema_type: str
    domain: str | None = None
    path: str | None = None
    schema_hash: str
    param_count: int = 0
    evidence_count: int = 0
    warning_count: int = 0
    generated_from_crawl_id: UUID | None = None
    generated_from_document_id: str | None = None
    created_at: datetime
    updated_at: datetime


class QuerySchemaListResponse(BaseModel):
    items: list[QuerySchemaListRecord]
    total: int
    limit: int
    offset: int


class QuerySchemaDetailRecord(QuerySchemaRecord):
    param_count: int = 0
    evidence_count: int = 0
    warning_count: int = 0


class QuerySchemaUpdateRequest(BaseModel):
    enabled: bool | None = None
    priority: int | None = None
