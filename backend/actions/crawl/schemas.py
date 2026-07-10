from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from actions.shared.quality.schemas import QualityWarning


class Input(BaseModel):
    urls: list[str] = Field(
        min_length=1,
        description="The URLs to crawl.",
    )


class CrawlPage(BaseModel):
    url: str
    success: bool
    status_code: int | None = None
    duration_seconds: float
    crawl_id: UUID | None = None
    artifact_ids: list[UUID] = Field(default_factory=list)
    html: str | None = None
    crawl: dict[str, Any] | None = None
    artifact_warnings: list[QualityWarning] = Field(
        default_factory=list,
        description="Artifact quality warnings for the captured page content.",
    )
    error: str | None = None


class CrawlStats(BaseModel):
    requested_urls: int
    succeeded: int
    failed: int
    duration_seconds: float


class CrawlOutput(BaseModel):
    stats: CrawlStats
    pages: list[CrawlPage]
