from typing import Literal

from pydantic import BaseModel, Field

from shared.crawl import CrawlMode, CrawlWait
from shared.quality.schemas import QualityWarning

ArtifactFormat = Literal["html", "crawl"]


class Input(BaseModel):
    urls: list[str] = Field(
        min_length=1,
        description="The URLs to scrape.",
    )
    mode: CrawlMode = Field(
        default="static",
        description="The crawl preset to use before writing artifacts.",
    )
    wait: CrawlWait = Field(
        default="none",
        description="The wait strategy to use before writing artifacts.",
    )
    concurrency: int = Field(
        ge=1,
        default=10,
        description="The number of pages to scrape in parallel.",
    )


class ScrapeArtifact(BaseModel):
    format: ArtifactFormat
    path: str
    bytes: int


class ScrapePage(BaseModel):
    url: str
    success: bool
    cached: bool = False
    status_code: int | None = None
    duration_seconds: float
    cache_dir: str
    html_path: str | None = None
    crawl_path: str | None = None
    warnings_path: str | None = None
    warnings: list[QualityWarning] = Field(default_factory=list)
    artifacts: list[ScrapeArtifact]
    error: str | None = None


class ScrapeStats(BaseModel):
    requested_urls: int
    succeeded: int
    failed: int
    cache_hits: int
    artifacts: int
    bytes_written: int
    duration_seconds: float


class ScrapeOutput(BaseModel):
    cache_root: str
    stats: ScrapeStats
    pages: list[ScrapePage]
