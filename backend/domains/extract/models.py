from typing import Any

from pydantic import BaseModel, Field

from domains.crawl import CrawlMode, CrawlWait
from domains.schema.models import SchemaType


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
    scrape_cached: bool
    schema_id: str
    schema_path: str
    schema_cached: bool


class ExtractOutput(BaseModel):
    url: str
    success: bool
    source: ExtractSource | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
