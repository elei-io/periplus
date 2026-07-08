from typing import Any

from pydantic import BaseModel, Field

from actions.shared.crawl import CrawlMode, CrawlWait
from actions.shared.quality.schemas import QualityWarning
from actions.shared.extract_schema.schemas import SchemaType


class Input(BaseModel):
    url: str = Field(description="The URL to crawl and extract structured data from.")
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

class ExtractSource(BaseModel):
    schema_id: str
    schema_type: SchemaType


class ExtractOutput(BaseModel):
    url: str
    success: bool
    source: ExtractSource | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[QualityWarning] = Field(default_factory=list)
    error: str | None = None
