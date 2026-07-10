from pydantic import BaseModel, Field

from actions.shared.cache import CacheOptions


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
    cache: CacheOptions | None = Field(
        default=None,
        description="Optional cache behavior overriding the matching CrawlPolicy.",
    )
    max_pages: int = Field(default=10_000, ge=1)
    max_links: int = Field(default=250_000, ge=1)


class IndexLink(BaseModel):
    source_url: str
    url: str
    text: str
    title: str
    depth: int
    link_index: int
    internal: bool


class IndexOutput(BaseModel):
    pages: int
    failed_pages: int
    discovered_links: int
    result_links: int
    links: list[IndexLink] = Field(default_factory=list)
