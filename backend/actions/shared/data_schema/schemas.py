from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from actions.shared.cache import CacheOptions

SchemaType = Literal["css", "xpath"]


class Input(BaseModel):
    url: str = Field(description="Sample URL used to generate and validate the extraction schema.")
    prompt: str = Field(description="Natural-language extraction instructions.")
    target_json_example: str | None = Field(
        default=None,
        description="Optional JSON example showing the desired extracted object shape. Generated when omitted.",
    )
    schema_type: SchemaType = Field(default="css", description="Crawl4AI schema selector type.")
    schema_id: str | None = Field(
        default=None,
        description="Optional stable identifier for the generated schema.",
    )
    cache: CacheOptions | None = Field(
        default=None,
        description="Optional cache behavior overriding the matching CrawlPolicy.",
    )


class SchemaOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_id: str
    schema_type: SchemaType
    path: str | None = None
    extraction_schema: dict = Field(serialization_alias="schema")
