from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from actions.shared.crawl import CrawlMode, CrawlWait
from actions.shared.quality.schemas import QualityWarning


class Input(BaseModel):
    urls: list[str] = Field(
        min_length=1,
        description="The URLs to crawl.",
    )
    mode: CrawlMode = Field(
        default="static",
        description="The crawl preset to use.",
    )
    wait: CrawlWait = Field(
        default="none",
        description="The wait strategy to use.",
    )
    concurrency: int = Field(
        ge=1,
        default=10,
        description="The number of pages to crawl in parallel.",
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
    warnings: list[QualityWarning] = Field(default_factory=list)
    error: str | None = None


class CrawlStats(BaseModel):
    requested_urls: int
    succeeded: int
    failed: int
    duration_seconds: float


class CrawlOutput(BaseModel):
    stats: CrawlStats
    pages: list[CrawlPage]
