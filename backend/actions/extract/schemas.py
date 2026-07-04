from typing import Any

from pydantic import BaseModel, Field

from shared.crawl import CrawlMode, CrawlWait
from shared.quality.schemas import QualityWarning
from shared.extract_schema.schemas import SchemaType


class Input(BaseModel):
    url: str = Field(description="The URL to scrape and extract structured data from.")
    prompt: str = Field(description="Natural-language extraction instructions.")
    target_json_example: str | None = Field(
        default=None,
        description="Optional JSON example showing the desired extracted object shape.",
    )
    schema_type: SchemaType = Field(default="css", description="Crawl4AI schema selector type.")
    mode: CrawlMode = Field(
        default="static",
        description="The crawl preset to use before extraction.",
    )
    wait: CrawlWait = Field(
        default="none",
        description="The wait strategy to use before extraction.",
    )
    refresh_schema: bool = Field(default=False, description="Regenerate the extraction schema.")


class ExtractSource(BaseModel):
    scrape_cache_dir: str
    html_path: str
    warnings_path: str | None = None
    scrape_cached: bool
    schema_id: str
    schema_path: str
    schema_cached: bool


class ExtractOutput(BaseModel):
    url: str
    success: bool
    source: ExtractSource | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[QualityWarning] = Field(default_factory=list)
    error: str | None = None
