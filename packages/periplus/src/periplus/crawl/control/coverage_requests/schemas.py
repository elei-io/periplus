from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, computed_field, model_validator

CoverageStatus = Literal["pending", "resolving", "ongoing", "completed", "failed"]


def within_allowed_sections(url: str, sections: list[str]) -> bool:
    if not sections:
        return True
    value = str(TypeAdapter(HttpUrl).validate_python(url)).split("?", 1)[0].split("#", 1)[0]
    return any(value == section or value.startswith(section + "/") for section in sections)


class CoverageRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["url", "description"]
    input: str = Field(min_length=1, max_length=4000)
    depth: int = Field(default=0, ge=0, le=2, strict=True)
    link_scope: Literal["internal", "external", "both"] = "internal"
    max_pages: int = Field(default=25, ge=1, le=1000, strict=True)
    allowed_sections: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_input(self):
        normalized = []
        for section in self.allowed_sections:
            if len(section) > 1000:
                raise ValueError("Each allowed section must be at most 1,000 characters.")
            url = TypeAdapter(HttpUrl).validate_python(section)
            if url.username or url.password or url.query is not None or url.fragment is not None:
                raise ValueError("Allowed sections must be public HTTP(S) URLs without credentials, queries, or fragments.")
            normalized.append(str(url).rstrip("/"))
        self.allowed_sections = list(dict.fromkeys(normalized))
        if self.kind == "url":
            url = TypeAdapter(HttpUrl).validate_python(self.input)
            if url.username or url.password:
                raise ValueError("URLs must not contain credentials.")
            self.input = str(url)
            if not within_allowed_sections(self.input, self.allowed_sections):
                raise ValueError("The starting URL must be within an allowed section.")
        return self


class CoverageProgress(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: str
    request_count: int
    pending_request_count: int
    acquisition_pending_count: int
    failed_request_count: int
    crawl_limit_reached: bool


    @computed_field
    @property
    def acquisition_settled_count(self) -> int:
        """Acquisition no longer pending; includes terminal failures/cancellations."""
        return max(0, self.request_count - self.acquisition_pending_count)

    @computed_field
    @property
    def navigation_pending_count(self) -> int:
        """Acquired requests still preparing navigation or evaluating outgoing edges."""
        return max(0, self.pending_request_count - self.acquisition_pending_count)


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
