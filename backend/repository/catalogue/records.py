"""Typed evidence and result records crossing the DuckLake boundary."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


_ATTEMPT_NAMESPACE = UUID("feef76f0-a91d-58f8-9533-e30c90a784b2")
_DOCUMENT_NAMESPACE = UUID("c33796c6-cb82-51bd-a666-2c9e495a90d9")
_PAGE_NAMESPACE = UUID("48462520-afc9-577b-a950-077c17bbcd35")


def canonical_json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def attempt_id_for(visit_id: UUID, attempt_index: int) -> UUID:
    if attempt_index < 0:
        raise ValueError("attempt_index must be non-negative")
    return uuid5(_ATTEMPT_NAMESPACE, f"{visit_id}:{attempt_index}")


def document_id_for(visit_id: UUID) -> UUID:
    return uuid5(_DOCUMENT_NAMESPACE, str(visit_id))


def page_id_for(normalized_url: str) -> UUID:
    if not normalized_url:
        raise ValueError("normalized_url must not be empty")
    return uuid5(_PAGE_NAMESPACE, f"v1:{normalized_url}")


class CatalogueRecord(BaseModel):
    model_config = ConfigDict(frozen=True)


class CrawlRecord(CatalogueRecord):
    crawl_id: UUID
    graph_id: UUID
    graph_config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    graph_config: JsonValue
    root_url_count: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime
    stop_reason: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_record(self) -> CrawlRecord:
        if self.finished_at < self.started_at:
            raise ValueError("crawl finished_at cannot precede started_at")
        digest = hashlib.sha256(canonical_json(self.graph_config).encode()).hexdigest()
        if digest != self.graph_config_hash:
            raise ValueError(
                "graph_config_hash must match canonical graph_config JSON"
            )
        return self


class VisitRecord(CatalogueRecord):
    visit_id: UUID
    crawl_id: UUID
    requested_url: str = Field(min_length=1)
    effective_url: str | None = Field(default=None, min_length=1)
    admitted_at: datetime
    started_at: datetime | None = None
    observed_at: datetime | None = None
    finished_at: datetime
    outcome: Literal["succeeded", "failed", "cancelled", "skipped"]
    status_code: int | None = Field(default=None, ge=100, le=599)
    document_id: UUID | None = None

    @model_validator(mode="after")
    def validate_record(self) -> VisitRecord:
        if self.started_at is not None and self.started_at < self.admitted_at:
            raise ValueError("visit started_at cannot precede admitted_at")
        if self.finished_at < (self.started_at or self.admitted_at):
            raise ValueError("visit finished_at precedes its start")
        if self.observed_at is not None:
            if self.started_at is None:
                raise ValueError("an observed visit must have started")
            if not self.started_at <= self.observed_at <= self.finished_at:
                raise ValueError("visit observed_at must fall within execution")
        if (self.document_id is not None) != (self.observed_at is not None):
            raise ValueError(
                "document_id and observed_at must be present together"
            )
        if self.document_id is not None:
            if self.outcome != "succeeded":
                raise ValueError("only a succeeded visit may produce a document")
            if self.document_id != document_id_for(self.visit_id):
                raise ValueError("document_id must be derived from visit_id")
        return self


class AttemptRecord(CatalogueRecord):
    attempt_id: UUID
    visit_id: UUID
    attempt_index: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime
    effective_url: str | None = Field(default=None, min_length=1)
    status_code: int | None = Field(default=None, ge=100, le=599)
    outcome: Literal["succeeded", "failed", "cancelled"]
    failure_stage: str | None = Field(default=None, max_length=128)
    failure_code: str | None = Field(default=None, max_length=128)
    failure_message: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_record(self) -> AttemptRecord:
        if self.attempt_id != attempt_id_for(self.visit_id, self.attempt_index):
            raise ValueError("attempt_id must be derived from visit_id and attempt_index")
        if self.finished_at < self.started_at:
            raise ValueError("attempt finished_at cannot precede started_at")
        failure_values = (
            self.failure_stage,
            self.failure_code,
            self.failure_message,
        )
        if self.outcome == "succeeded" and any(
            value is not None for value in failure_values
        ):
            raise ValueError("a succeeded attempt cannot contain failure fields")
        if self.outcome == "failed" and self.failure_code is None:
            raise ValueError("a failed attempt requires failure_code")
        return self


class StepRecord(CatalogueRecord):
    attempt_id: UUID
    step_index: int = Field(ge=0)
    action: Literal["wait_dynamic", "wait_fixed", "scroll", "expand"]
    parameters: JsonValue
    started_at: datetime
    duration_ms: int = Field(ge=0)
    outcome: Literal["succeeded", "failed", "cancelled"]
    stopping_reason: str | None = Field(default=None, max_length=128)
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_record(self) -> StepRecord:
        if self.outcome == "succeeded" and (
            self.error_code is not None or self.error_message is not None
        ):
            raise ValueError("a succeeded step cannot contain error fields")
        if self.outcome == "failed" and self.error_code is None:
            raise ValueError("a failed step requires error_code")
        return self


class DocumentRecord(CatalogueRecord):
    document_id: UUID
    visit_id: UUID
    attempt_id: UUID
    observed_at: datetime
    representation: Literal["response_body", "rendered_html"]
    declared_media_type: str | None = Field(default=None, min_length=1)
    detected_media_type: str = Field(min_length=1)
    charset: str | None = Field(default=None, min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_bytes: int = Field(ge=0)
    object_key: str = Field(min_length=1)
    storage_encoding: str = Field(min_length=1)
    stored_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_record(self) -> DocumentRecord:
        if self.document_id != document_id_for(self.visit_id):
            raise ValueError("document_id must be derived from visit_id")
        if self.object_key.startswith("/") or ".." in self.object_key.split("/"):
            raise ValueError("object_key must be repository-relative")
        return self


class VisitEvidence(CatalogueRecord):
    visit: VisitRecord
    attempts: tuple[AttemptRecord, ...]
    steps: tuple[StepRecord, ...] = ()
    document: DocumentRecord | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> VisitEvidence:
        if self.document is None:
            if self.visit.document_id is not None:
                raise ValueError("visit document_id requires document evidence")
        else:
            if self.visit.document_id != self.document.document_id:
                raise ValueError("visit and document identities differ")
            if self.document.visit_id != self.visit.visit_id:
                raise ValueError("document belongs to another visit")
        if any(attempt.visit_id != self.visit.visit_id for attempt in self.attempts):
            raise ValueError("attempt belongs to another visit")
        indexes = tuple(attempt.attempt_index for attempt in self.attempts)
        if indexes != tuple(range(len(indexes))):
            raise ValueError("attempt indexes must be contiguous from zero")
        attempt_ids = {attempt.attempt_id for attempt in self.attempts}
        if self.document is not None:
            if self.document.attempt_id not in attempt_ids:
                raise ValueError("document attempt is absent from visit evidence")
            successful = next(
                attempt
                for attempt in self.attempts
                if attempt.attempt_id == self.document.attempt_id
            )
            if successful.outcome != "succeeded":
                raise ValueError("document attempt must have succeeded")
        grouped: dict[UUID, list[int]] = {}
        for step in self.steps:
            if step.attempt_id not in attempt_ids:
                raise ValueError("step belongs to an absent attempt")
            grouped.setdefault(step.attempt_id, []).append(step.step_index)
        if any(indexes != list(range(len(indexes))) for indexes in grouped.values()):
            raise ValueError("step indexes must be contiguous from zero per attempt")
        return self


class IngestionWriteResult(CatalogueRecord):
    kind: Literal["crawl", "visit"]
    identity: UUID
    created: bool
    repository_snapshot: int
