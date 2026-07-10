from pydantic import BaseModel, Field, model_validator

from actions.shared.cache import CacheOptions
from actions.index.limits import IndexLimits


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
    max_pages: int = Field(default=100_000, ge=1)
    max_links: int = Field(default=5_000_000, ge=1)
    max_temp_bytes: int = Field(default=10 * 1024 * 1024 * 1024, ge=1)

    @model_validator(mode="after")
    def deployment_budgets(self) -> Input:
        limits = IndexLimits.from_env()
        for name in ("max_pages", "max_links", "max_temp_bytes"):
            if name not in self.model_fields_set:
                setattr(self, name, min(getattr(self, name), getattr(limits, name)))
        limits.validate(
            max_pages=self.max_pages,
            max_links=self.max_links,
            max_temp_bytes=self.max_temp_bytes,
        )
        return self


class IndexOutput(BaseModel):
    pages: int
    failed_pages: int
    discovered_links: int
    result_links: int
