from typing import Any

from pydantic import BaseModel, Field

from actions.shared.crawl import CrawlMode, CrawlWait
from actions.shared.quality.schemas import QualityWarning


class Input(BaseModel):
    urls: list[str] = Field(
        min_length=1,
        description="The URLs to scrape.",
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
        description="The number of pages to scrape in parallel.",
    )


class ScrapePage(BaseModel):
    url: str
    success: bool
    status_code: int | None = None
    duration_seconds: float
    html: str | None = None
    crawl: dict[str, Any] | None = None
    warnings: list[QualityWarning] = Field(default_factory=list)
    error: str | None = None


class ScrapeStats(BaseModel):
    requested_urls: int
    succeeded: int
    failed: int
    duration_seconds: float


class ScrapeOutput(BaseModel):
    stats: ScrapeStats
    pages: list[ScrapePage]
