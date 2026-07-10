from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from actions.shared.cache import CacheOptions
from actions.shared.quality.schemas import QualityWarning


class Input(BaseModel):
    urls: list[str] = Field(
        min_length=1,
        max_length=10_000,
        description="The URLs to crawl.",
    )
    cache: CacheOptions | None = Field(
        default=None,
        description="Optional cache behavior overriding the matching CrawlPolicy.",
    )


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


class CrawlStats(BaseModel):
    requested_urls: int
    succeeded: int
    failed: int
    duration_seconds: float


class CrawlOutput(BaseModel):
    stats: CrawlStats
    pages: list[CrawlPage]
