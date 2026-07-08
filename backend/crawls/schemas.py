from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from artifacts.schemas import ArtifactListRecord


class CrawlRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url_id: UUID
    task_run_id: UUID
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    inputs_json: dict[str, Any]
    input_hash: str
    success: bool
    status_code: int | None = None
    redirects_json: dict[str, Any]
    errors_json: dict[str, Any]
    retry_count: int
    warnings_json: dict[str, Any]
    meta: dict[str, Any]
    created_at: datetime


class CrawlListRecord(BaseModel):
    id: UUID
    url_id: UUID
    task_run_id: UUID
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    input_hash: str
    success: bool
    status_code: int | None = None
    retry_count: int
    warning_count: int = 0
    error_message: str | None = None
    artifact_count: int = 0
    url: str
    normalized_url: str
    domain: str
    path_name: str


class CrawlListResponse(BaseModel):
    items: list[CrawlListRecord]
    total: int
    limit: int
    offset: int


class CrawlDetailRecord(CrawlListRecord):
    inputs_json: dict[str, Any]
    redirects_json: dict[str, Any]
    errors_json: dict[str, Any]
    warnings_json: dict[str, Any]
    meta: dict[str, Any]
    created_at: datetime
    artifacts: list[ArtifactListRecord]
