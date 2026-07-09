from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from actions.shared.crawl import CrawlMode, CrawlWait

PaginationKind = Literal["query"]


class Input(BaseModel):
    url: str
    max_pages: int = Field(default=5, ge=1, le=25)
    mode: CrawlMode = "app"
    wait: CrawlWait = "stable"
    reuse_existing: bool = True


class PaginationPlan(BaseModel):
    schema_id: UUID | None = None
    kind: PaginationKind
    next_button_selector: str | None = None
    item_selector: str
    expected_max_item_count: int | None = None
    query_param_key: str
    query_param_value_template: str
    start_value: int
    value_step: int = 1
    match: str
    reused: bool = False
    confidence: float | None = None
    evidence: list[str] = Field(default_factory=list)


class PaginatedPage(BaseModel):
    index: int
    url: str
    crawl_id: UUID | None = None
    artifact_ids: list[UUID] = Field(default_factory=list)
    item_count: int
    new_item_count: int
    success: bool
    error: str | None = None


class PaginateOutput(BaseModel):
    url: str
    pages: list[PaginatedPage]
    plan: PaginationPlan | None = None
    stopped_reason: str
    warnings: list[str] = Field(default_factory=list)
