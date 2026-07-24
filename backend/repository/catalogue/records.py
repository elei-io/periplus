"""Typed records crossing the DuckLake repository boundary."""

from __future__ import annotations

from datetime import datetime
import hashlib
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID
import tldextract

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class CatalogueRecord(BaseModel):
    """Immutable base for durable catalogue values."""

    model_config = ConfigDict(frozen=True)


_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


class ArtifactRecord(CatalogueRecord):
    artifact_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_key: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    response_media_type: str = Field(min_length=1)
    detected_media_type: str = Field(min_length=1)
    detector_name: str = Field(min_length=1)
    detector_version: str = Field(min_length=1)
    detection_confidence: float = Field(ge=0, le=1)
    created_at: datetime


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


class UrlRecord(CatalogueRecord):
    url_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_url: str = Field(min_length=1)
    scheme: str = Field(min_length=1)
    host: str = Field(min_length=1)
    port: int = Field(ge=0, le=65535)
    registrable_domain: str = Field(min_length=1)
    path: str = Field(min_length=1)
    query: str

    @classmethod
    def from_normalized_url(cls, normalized_url: str) -> UrlRecord:
        parsed = urlparse(normalized_url)
        host = (parsed.hostname or "").lower()
        scheme = parsed.scheme.lower()
        try:
            ip_address(host)
        except ValueError:
            extracted = _TLD_EXTRACT(host)
            registrable_domain = extracted.top_domain_under_public_suffix or host
        else:
            registrable_domain = host
        return cls(
            url_id=hashlib.sha256(normalized_url.encode()).hexdigest(),
            normalized_url=normalized_url,
            scheme=scheme,
            host=host,
            port=parsed.port or {"http": 80, "https": 443}.get(scheme, 0),
            registrable_domain=registrable_domain,
            path=parsed.path or "/",
            query=parsed.query,
        )


class CrawlRecord(CatalogueRecord):
    crawl_id: UUID
    document_id: str | None = Field(default=None, min_length=1)
    artifact_id: str | None = Field(default=None, min_length=1)
    graph_id: UUID
    graph_run_id: UUID
    graph_node_id: UUID
    crawl_request_id: UUID
    source_crawl_id: UUID | None = None
    source_edge_id: UUID | None = None
    requested_url_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    final_url_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    captured_at: datetime
    status_code: int | None = Field(default=None, ge=100, le=599)
    duration_ms: int | None = Field(default=None, ge=0)
    response_media_type: str | None = Field(default=None, min_length=1)
    response_filename: str | None = Field(default=None, min_length=1, max_length=1024)
    policy_config_hash: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    policy_config_json: dict[str, JsonValue] = Field(default_factory=dict)
    crawl_policy_id: UUID | None = None
    outcome: Literal["success", "skipped", "failed"]
    failure_code: str | None = None
    failure_stage: str | None = None
    failure_retryable: bool | None = None
    failure_detail: str | None = Field(default=None, max_length=2048)
    @model_validator(mode="after")
    def validate_outcome(self) -> CrawlRecord:
        has_failure = self.failure_code is not None
        failure_fields = (
            self.failure_code,
            self.failure_stage,
            self.failure_retryable,
            self.failure_detail,
        )
        if self.outcome == "success" and any(value is not None for value in failure_fields):
            raise ValueError("a successful crawl cannot record acquisition failure fields")
        if self.outcome == "failed" and not all(
            value is not None for value in failure_fields
        ):
            raise ValueError("a failed crawl requires complete failure provenance")
        if self.outcome == "skipped" and any(value is not None for value in failure_fields):
            raise ValueError("a skipped crawl cannot record acquisition failure fields")
        captured_count = sum(
            identity is not None for identity in (self.document_id, self.artifact_id)
        )
        if captured_count > 1:
            raise ValueError("a crawl cannot reference both a document and an artifact")
        if captured_count == 0 and self.outcome not in {"failed", "skipped"}:
            raise ValueError("a contentless crawl must be failed or skipped")
        if captured_count == 1 and self.outcome == "failed":
            raise ValueError("a crawl with captured content cannot have a failed outcome")
        if self.artifact_id is not None and self.response_media_type is None:
            raise ValueError("an artifact crawl requires its response media type")
        return self


class CrawlAttemptRecord(CatalogueRecord):
    crawl_id: UUID
    attempt_number: int = Field(ge=1)
    started_at: datetime
    completed_at: datetime
    requested_url_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    final_url_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status_code: int | None = Field(default=None, ge=100, le=599)
    response_media_type: str | None = Field(default=None, min_length=1)
    outcome: Literal["success", "retry", "skipped", "failed"]
    failure_code: str | None = None
    retry_after_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_timing(self) -> CrawlAttemptRecord:
        if self.completed_at < self.started_at:
            raise ValueError("crawl attempt completed_at cannot precede started_at")
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


class CrawlStepRecord(CatalogueRecord):
    crawl_id: UUID
    attempt_number: int = Field(ge=1)
    step_ordinal: int = Field(ge=1)
    method: Literal["wait_dynamic", "wait_fixed", "scroll", "expand"]
    method_version: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_json: dict[str, JsonValue]
    started_at: datetime
    duration_ms: int = Field(ge=0)
    iterations: int = Field(ge=0)
    stop_reason: str = Field(min_length=1)
    before_element_count: int = Field(ge=0)
    after_element_count: int = Field(ge=0)
    before_text_chars: int = Field(ge=0)
    after_text_chars: int = Field(ge=0)
    before_link_count: int = Field(ge=0)
    after_link_count: int = Field(ge=0)
    before_scroll_height: int = Field(ge=0)
    after_scroll_height: int = Field(ge=0)


class CatalogueWriteResult(CatalogueRecord):
    document_id: str | None
    artifact_id: str | None = None
    crawl_id: UUID
    document_created: bool
    artifact_created: bool = False
    crawl_created: bool
    repository_snapshot: int
