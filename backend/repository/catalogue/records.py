"""Typed records crossing the DuckLake repository boundary."""

from __future__ import annotations

from datetime import datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from crawl4ai.utils import get_base_domain
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
    page_url: str = Field(min_length=1)
    url_scheme: str = Field(min_length=1)
    url_host: str = Field(min_length=1)
    url_port: int = Field(ge=0, le=65535)
    url_registrable_domain: str = Field(min_length=1)
    url_path: str = Field(min_length=1)
    url_query: str
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

    @model_validator(mode="before")
    @classmethod
    def derive_page_url_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        result = dict(value)
        page_url = str(result.get("final_url") or result.get("normalized_url") or "")
        parsed = urlparse(page_url)
        host = (parsed.hostname or "").lower()
        scheme = parsed.scheme.lower()
        try:
            ip_address(host)
        except ValueError:
            registrable_domain = get_base_domain(page_url) or host
        else:
            registrable_domain = host
        result.update(
            page_url=page_url,
            url_scheme=scheme,
            url_host=host,
            url_port=parsed.port or {"http": 80, "https": 443}.get(scheme, 0),
            url_registrable_domain=registrable_domain,
            url_path=parsed.path or "/",
            url_query=parsed.query,
        )
        return result

    @model_validator(mode="after")
    def validate_documentless_failure(self) -> CrawlRecord:
        if self.document_id is None and not self.errors_json:
            raise ValueError("a crawl without a document must record an acquisition error")
        return self


class ElementRecord(CatalogueRecord):
    element_index: int = Field(ge=0)
    parent_index: int | None = Field(default=None, ge=0)
    subtree_end_index: int = Field(ge=0)
    depth: int = Field(ge=0)
    tag: str = Field(min_length=1)
    namespace_uri: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    text_direct: str
    text_tail: str


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
