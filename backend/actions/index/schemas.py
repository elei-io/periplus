from pydantic import BaseModel, Field

from shared.crawl import CrawlMode, CrawlWait


class Input(BaseModel):
    url: str = Field(
        description="The URL to crawl.",
    )
    max_depth: int = Field(
        ge=0,
        default=0,
        description="The maximum depth to crawl the website.",
    )
    dedupe: bool = Field(
        default=False,
        description="Whether to deduplicate links by URL",
    )
    concurrency: int = Field(
        ge=1,
        default=10,
        description="The number of concurrent requests to make",
    )
    mode: CrawlMode = Field(
        default="static", description="The crawl preset to use for indexing."
    )
    wait: CrawlWait = Field(
        default="none",
        description="The wait strategy to use for indexing.",
    )
    include_crawl: list[str] = Field(
        default_factory=list,
        description="The URLs to include in the crawl.",
    )
    exclude_crawl: list[str] = Field(
        default_factory=list,
        description="The URLs to exclude from the crawl.",
    )
    include_result: list[str] = Field(
        default_factory=list,
        description="The URLs to include in the result.",
    )
    exclude_result: list[str] = Field(
        default_factory=list,
        description="The URLs to exclude from the result.",
    )


class IndexLink(BaseModel):
    source_url: str
    url: str
    text: str
    title: str
    depth: int
    link_index: int
    internal: bool
