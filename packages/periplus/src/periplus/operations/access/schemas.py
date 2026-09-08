from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Capability = Literal["crawl", "assistant", "sql"]

class RatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    requests: int = Field(default=60, ge=1, le=1000000)
    window_seconds: int = Field(default=60, ge=1, le=86400)

class CrawlPolicy(RatePolicy):
    requests: int = Field(default=6, ge=1, le=1000000)
    page_budgets: list[int] = Field(default_factory=lambda: [5, 25, 100, 500, 1000], min_length=1, max_length=20)
    default_page_budget: int = 25
    max_depths: list[int] = Field(default_factory=lambda: [0, 1, 2], min_length=1, max_length=20)
    default_max_depth: int = 1
    retention_seconds: list[int | None] = Field(default_factory=lambda: [None, 604800, 2592000, 7776000, 31536000], min_length=1, max_length=20)
    default_retention_seconds: int | None = None

    @model_validator(mode="after")
    def choices(self):
        for options, default, low, high in ((self.page_budgets, self.default_page_budget, 1, 100000),
                (self.max_depths, self.default_max_depth, 0, 100),
                (self.retention_seconds, self.default_retention_seconds, 1, 315360000)):
            if len(set(options)) != len(options) or default not in options:
                raise ValueError("Options must be unique and contain their default")
            if any(value is not None and (value < low or value > high) for value in options):
                raise ValueError("Option outside supported request bounds")
        return self

class QueryLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_rows: int = Field(default=1000, ge=1, le=10000)
    max_duration_seconds: int = Field(default=20, ge=1, le=120)
    max_result_bytes: int = Field(default=8 * 1024 * 1024, ge=1024 * 1024, le=64 * 1024 * 1024)

class SqlPolicy(RatePolicy, QueryLimits):
    pass

class AccessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    crawl: CrawlPolicy = Field(default_factory=CrawlPolicy)
    assistant: RatePolicy = Field(default_factory=lambda: RatePolicy(requests=10))
    sql: SqlPolicy = Field(default_factory=SqlPolicy)

class AccessView(AccessPolicy):
    version: int

class AccessEdit(AccessPolicy):
    expected_version: int = Field(ge=1)
