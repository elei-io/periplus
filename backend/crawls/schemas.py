from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
