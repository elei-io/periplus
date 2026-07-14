"""Typed records crossing the DuckLake repository boundary."""

from __future__ import annotations

from datetime import datetime
from ipaddress import ip_address
from typing import Any, Literal
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
    quality_schema_version: int = Field(ge=1)
    html_character_count: int = Field(ge=0)
    visible_text_chars: int = Field(ge=0)
    script_count: int = Field(ge=0)
    app_marker_count: int = Field(ge=0)
    lazy_marker_count: int = Field(ge=0)
    interaction_marker_count: int = Field(ge=0)
    button_count: int = Field(ge=0)
    form_count: int = Field(ge=0)
    input_count: int = Field(ge=0)
    anchor_count: int = Field(ge=0)
    quality_flags_json: tuple[str, ...] = ()
    created_at: datetime


class CrawlRecord(CatalogueRecord):
    crawl_id: UUID
    document_id: str | None = Field(default=None, min_length=1)
    graph_id: UUID
    graph_run_id: UUID
    graph_node_id: UUID
    crawl_request_id: UUID
    purpose: Literal["use", "sample"] = "use"
    trial_id: UUID | None = None
    source_crawl_id: UUID | None = None
    source_edge_id: UUID | None = None
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
    domain_group: str = Field(min_length=1)
    profile: Literal["http", "browser", "firecrawl"]
    template: str = Field(min_length=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_json: dict[str, JsonValue]
    crawl_policy_id: UUID | None = None
    crawl_policy_revision: int | None = Field(default=None, ge=1)
    outcome: Literal["success", "partial", "failed"]
    failure_code: str | None = None
    failure_stage: str | None = None
    failure_retryable: bool | None = None
    failure_detail: str | None = Field(default=None, max_length=2048)
    trial_sampler_version: int | None = Field(default=None, ge=1)
    trial_sample_rate: float | None = Field(default=None, ge=0, le=1)
    trial_candidate_strategy: str | None = None
    trial_candidate_template: str | None = None
    trial_template_registry_version: int | None = Field(default=None, ge=1)

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
    def validate_outcome_and_trial(self) -> CrawlRecord:
        has_failure = self.failure_code is not None
        failure_fields = (
            self.failure_code,
            self.failure_stage,
            self.failure_retryable,
            self.failure_detail,
        )
        if self.outcome == "success" and any(value is not None for value in failure_fields):
            raise ValueError("a successful crawl cannot record acquisition failure fields")
        if self.outcome != "success" and not all(
            value is not None for value in failure_fields
        ):
            raise ValueError("a non-successful crawl requires complete failure provenance")
        if self.document_id is None and (self.outcome != "failed" or not has_failure):
            raise ValueError("a documentless crawl must record a failed acquisition")
        if self.document_id is not None and self.outcome == "failed":
            raise ValueError("a crawl with captured HTML cannot have a failed outcome")
        if self.purpose == "sample" and self.trial_id is None:
            raise ValueError("a sample crawl must record a trial_id")
        trial_fields = (
            self.trial_sampler_version,
            self.trial_sample_rate,
            self.trial_candidate_strategy,
            self.trial_candidate_template,
            self.trial_template_registry_version,
        )
        if self.trial_id is None and any(value is not None for value in trial_fields):
            raise ValueError("trial metadata requires a trial_id")
        if self.trial_id is not None and not all(value is not None for value in trial_fields):
            raise ValueError("a trial crawl requires complete sampling provenance")
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


class CatalogueWriteResult(CatalogueRecord):
    document_id: str | None
    crawl_id: UUID
    document_created: bool
    crawl_created: bool
    repository_snapshot: int
