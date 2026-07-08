from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ExtractSchemaRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    schema_json: dict[str, Any]
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
