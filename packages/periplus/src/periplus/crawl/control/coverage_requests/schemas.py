from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, model_validator

CoverageStatus = Literal["pending", "resolving", "ongoing", "completed", "failed"]


class CoverageRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["url", "description"]
    input: str = Field(min_length=1, max_length=4000)
    depth: int = Field(default=0, ge=0, le=2, strict=True)
    link_scope: Literal["internal", "external", "both"] = "internal"
    max_pages: int = Field(default=25, ge=1, le=1000, strict=True)

    @model_validator(mode="after")
    def validate_input(self):
        if self.kind == "url":
            url = TypeAdapter(HttpUrl).validate_python(self.input)
            if url.username or url.password:
                raise ValueError("URLs must not contain credentials.")
            self.input = str(url)
        return self


class CoverageProgress(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: str
    request_count: int
    pending_request_count: int
    failed_request_count: int
    crawl_limit_reached: bool


class CoverageRequestRead(CoverageRequestCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: CoverageStatus
    created_at: datetime
    completed_at: datetime | None
    run_id: UUID | None = None
    resolved_urls: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    error: str | None = None
    retry_at: datetime | None = None
    progress: CoverageProgress | None = None


class CoverageRequestPage(BaseModel):
    items: list[CoverageRequestRead]
    total: int
    limit: int
    offset: int
