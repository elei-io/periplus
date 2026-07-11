"""Typed records crossing the DuckLake repository boundary."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class CatalogueRecord(BaseModel):
    """Immutable base for durable catalogue values."""

    model_config = ConfigDict(frozen=True)


class DocumentRecord(CatalogueRecord):
    document_id: str = Field(min_length=1)
    html_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    html_object_key: str = Field(min_length=1)
    html_content_type: str = Field(min_length=1)
    html_encoding: str = Field(min_length=1)
    html_size_bytes: int = Field(ge=0)
    html_compressed_size_bytes: int = Field(ge=0)
    compression: str = Field(min_length=1)
    dom_schema_version: int = Field(ge=1)
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    parser_options_hash: str = Field(min_length=1)
    element_count: int = Field(ge=0)
    created_at: datetime


class CrawlRecord(CatalogueRecord):
    crawl_id: UUID
    document_id: str | None = Field(default=None, min_length=1)
    run_id: UUID
    task_id: UUID
    task_revision: int = Field(ge=1)
    primitive: str = Field(min_length=1)
    requested_url: str = Field(min_length=1)
    normalized_url: str = Field(min_length=1)
    final_url: str | None = None
    captured_at: datetime
    status_code: int | None = Field(default=None, ge=100, le=599)
    duration_ms: int | None = Field(default=None, ge=0)
    input_json: dict[str, JsonValue]
    input_hash: str = Field(min_length=1)
    crawl_policy_id: UUID | None = None
    crawl_policy_revision: int | None = Field(default=None, ge=1)
    data_schema_id: UUID | None = None
    query_schema_id: UUID | None = None
    warnings_json: list[JsonValue] = Field(default_factory=list)
    errors_json: list[JsonValue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_documentless_failure(self) -> CrawlRecord:
        if self.document_id is None and not self.errors_json:
            raise ValueError("a crawl without a document must record an acquisition error")
        return self


class ElementRecord(CatalogueRecord):
    element_index: int = Field(ge=0)
    parent_index: int | None = Field(default=None, ge=0)
    tag: str = Field(min_length=1)
    namespace_uri: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    text: str | None = None
    tail: str | None = None


class LinkRecord(CatalogueRecord):
    document_id: str
    element_index: int
    href: str
    text: str | None = None
    title: str | None = None


class CatalogueWriteResult(CatalogueRecord):
    document_id: str | None
    crawl_id: UUID
    document_created: bool
    crawl_created: bool
    repository_snapshot: int
