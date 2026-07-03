from typing import Literal

from pydantic import BaseModel, Field

OutputFormat = Literal["html", "markdown", "pdf"]
ArtifactFormat = Literal["html", "markdown", "pdf", "image"]


class Input(BaseModel):
    urls: list[str] = Field(
        min_length=1,
        description="The URLs to scrape.",
    )
    download_images: bool = Field(
        default=False,
        description="Whether to download every image discovered on each scraped page.",
    )
    output_formats: list[OutputFormat] = Field(
        default_factory=lambda: ["html"],
        min_length=1,
        description="Page artifact formats to write.",
    )


class ScrapeArtifact(BaseModel):
    format: ArtifactFormat
    path: str
    source_url: str | None = None
    bytes: int


class ScrapePage(BaseModel):
    url: str
    success: bool
    cached: bool = False
    status_code: int | None = None
    duration_seconds: float
    cache_dir: str
    artifacts: list[ScrapeArtifact]
    error: str | None = None


class ScrapeStats(BaseModel):
    requested_urls: int
    succeeded: int
    failed: int
    cache_hits: int
    artifacts: int
    images_downloaded: int
    bytes_written: int
    duration_seconds: float


class ScrapeOutput(BaseModel):
    cache_root: str
    stats: ScrapeStats
    pages: list[ScrapePage]
