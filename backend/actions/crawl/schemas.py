from typing import Any
from uuid import UUID

from pydantic import BaseModel


class CrawlPage(BaseModel):
    url: str
    success: bool
    status_code: int | None = None
    duration_seconds: float
    crawl_id: UUID | None = None
    document_id: str | None = None
    repository_snapshot: int | None = None
    repository_crawl_created: bool | None = None
    html: str | None = None
    crawl: dict[str, Any] | None = None
    error: str | None = None
    failure_code: str | None = None
    failure_stage: str | None = None
    failure_retryable: bool | None = None
