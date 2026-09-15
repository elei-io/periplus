"""Frozen import intent and bounded operational checkpoints."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from periplus.urls import normalize_url
from periplus.ingestion.archive import ArchiveEvent
from periplus.ingestion.common_crawl import CommonCrawlRecord


class ImportSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset: str = Field(pattern=r"^CC-MAIN-[0-9]{4}-[0-9]{2}$")
    urls: tuple[Annotated[str, Field(max_length=4096)], ...] = Field(
        min_length=1, max_length=100
    )
    captured_from: AwareDatetime
    captured_until: AwareDatetime
    max_download_bytes: int = Field(
        default=64 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024
    )

    @field_validator("urls")
    @classmethod
    def exact_urls(cls, urls: tuple[str, ...]) -> tuple[str, ...]:
        values = tuple(dict.fromkeys(normalize_url(url) for url in urls))
        if any("*" in url for url in values):
            raise ValueError("provide explicit URLs, not wildcard patterns")
        return values

    @model_validator(mode="after")
    def dates(self) -> ImportSpec:
        if self.captured_until < self.captured_from:
            raise ValueError("capture end must be on or after capture start")
        return self


class ImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str
    status: Literal["published", "missing", "unsupported", "retired"]
    capture_id: UUID | None = None
    captured_at: datetime | None = None
    content_sha256: str | None = None
    stored_bytes: int = 0
    already_archived: bool = False
    detail: str | None = None


class ImportProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cursor: int = 0
    current: CommonCrawlRecord | None = None
    archive_ref: ArchiveEvent | None = None
    already_archived: bool = False
    # Charged before each remote attempt, including retries. It is a conservative
    # archive-download allowance, not measured network traffic or S3 growth.
    reserved_download_bytes: int = 0
    results: tuple[ImportResult, ...] = ()


class ImportView(BaseModel):
    id: UUID
    specification: ImportSpec
    status: Literal["queued", "running", "blocked", "completed", "cancelled"]
    progress: ImportProgress
    error: str | None
    created_at: datetime
    updated_at: datetime
    revision: int


class CreateImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    specification: ImportSpec
