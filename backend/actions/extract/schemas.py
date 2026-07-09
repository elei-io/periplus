from typing import Any

from pydantic import BaseModel, Field

from actions.shared.crawl import CrawlMode, CrawlWait
from actions.shared.quality.schemas import QualityWarning
from actions.shared.data_schema.schemas import SchemaType
from actions.shared.query_schema.schemas import QueryParamOutput


class Input(BaseModel):
    url: str = Field(description="The URL to crawl and extract structured data from.")
    extract_data: bool = Field(default=True, description="Extract structured data using a DataSchema.")
    extract_query_params: bool = Field(default=True, description="Extract query parameters using a QuerySchema.")
    prompt: str | None = Field(default=None, description="Natural-language data extraction instructions.")
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
    match: str | None = Field(
        default=None,
        description="Optional data schema match pattern for durable schema reuse.",
    )


class ExtractSource(BaseModel):
    schema_id: str
    schema_type: SchemaType


class ExtractOutput(BaseModel):
    url: str
    success: bool
    source: ExtractSource | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    query_params: QueryParamOutput | None = None
    warnings: list[QualityWarning] = Field(default_factory=list)
    error: str | None = None
