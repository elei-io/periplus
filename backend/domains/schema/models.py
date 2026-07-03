from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from domains.crawl import CrawlMode, CrawlWait

SchemaType = Literal["css", "xpath"]


class Input(BaseModel):
    url: str = Field(description="Sample URL used to generate and validate the extraction schema.")
    prompt: str = Field(description="Natural-language extraction instructions.")
    target_json_example: str | None = Field(
        default=None,
        description="Optional JSON example showing the desired extracted object shape. Generated when omitted.",
    )
    schema_type: SchemaType = Field(default="css", description="Crawl4AI schema selector type.")
    cache_key: str | None = Field(
        default=None,
        description="Optional stable cache key for a known reusable schema.",
    )
    refresh: bool = Field(default=False, description="Regenerate the schema even if it is cached.")
    mode: CrawlMode = Field(
        default="static",
        description="The crawl preset to use when schema generation needs to fetch the page.",
    )
    wait: CrawlWait = Field(
        default="none",
        description="The wait strategy to use when schema generation needs to fetch the page.",
    )


class SchemaOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_id: str
    schema_type: SchemaType
    path: str
    cached: bool
    extraction_schema: dict = Field(serialization_alias="schema")
