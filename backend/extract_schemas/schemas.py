from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExtractSchemaRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    identity_key: str
    match: str
    enabled: bool
    priority: int
    prompt: str
    prompt_hash: str
    schema_type: str
    target_json_hash: str | None = None
    domain: str | None = None
    path: str | None = None
    extraction_schema: dict[str, Any] = Field(alias="schema_json")
    schema_hash: str
    generated_from_crawl_id: UUID | None = None
    generated_from_artifact_id: UUID | None = None
    generated_by_task_run_id: UUID | None = None
    inputs_json: dict[str, Any]
    validation_status: str | None = None
    failure_count: int
    last_failed_at: datetime | None = None
    last_error: str | None = None
    warnings_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ExtractSchemaListRecord(BaseModel):
    id: UUID
    match: str
    enabled: bool
    priority: int
    prompt: str
    prompt_hash: str
    schema_type: str
    target_json_hash: str | None = None
    domain: str | None = None
    path: str | None = None
    schema_hash: str
    validation_status: str | None = None
    failure_count: int
    last_failed_at: datetime | None = None
    last_error: str | None = None
    task_run_count: int = 0
    warning_count: int = 0
    created_at: datetime
    updated_at: datetime


class ExtractSchemaListResponse(BaseModel):
    items: list[ExtractSchemaListRecord]
    total: int
    limit: int
    offset: int
    summary: "ExtractSchemaSummary"


class ExtractSchemaSummary(BaseModel):
    total_schemas: int = 0
    enabled_schemas: int = 0
    used_schemas: int = 0
    total_schema_uses: int = 0
    reused_schema_uses: int = 0
    reuse_rate: float = 0
    avg_uses_per_used_schema: float = 0
    failing_schemas: int = 0


class ExtractSchemaDetailRecord(ExtractSchemaRecord):
    task_run_count: int = 0
    warning_count: int = 0


class ExtractSchemaUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    match: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    extraction_schema: dict[str, Any] | None = Field(default=None, alias="schema_json")
    validation_status: str | None = None
