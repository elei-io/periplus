from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PaginationSchemaRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    identity_key: str
    match: str
    enabled: bool
    priority: int
    next_button_selector: str | None = None
    item_selector: str
    expected_max_item_count: int | None = None
    query_param_key: str
    query_param_value_template: str
    start_value: int
    value_step: int
    domain: str | None = None
    path: str | None = None
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


class PaginationSchemaUpdateRequest(BaseModel):
    match: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    next_button_selector: str | None = None
    item_selector: str | None = None
    expected_max_item_count: int | None = Field(default=None, ge=0)
    query_param_key: str | None = None
    query_param_value_template: str | None = None
    start_value: int | None = None
    value_step: int | None = Field(default=None, ge=1)
    validation_status: str | None = None
