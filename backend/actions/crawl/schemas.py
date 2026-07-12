from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from actions.shared.quality.schemas import QualityWarning


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
    quality_warnings: list[QualityWarning] = Field(
        default_factory=list,
        description="Quality warnings for the captured page content.",
    )
    error: str | None = None
