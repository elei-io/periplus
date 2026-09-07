"""Typed evidence and result records crossing the DuckLake boundary."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from periplus.crawl.control.domain_policies.schemas import DomainPolicySnapshot


_ATTEMPT_NAMESPACE = UUID("feef76f0-a91d-58f8-9533-e30c90a784b2")
_DOCUMENT_NAMESPACE = UUID("c33796c6-cb82-51bd-a666-2c9e495a90d9")
_LINK_NAMESPACE = UUID("64df434d-a150-56df-9593-f673c7cc9a61")
_LINK_OCCURRENCE_NAMESPACE = UUID(
    "f48cf2ce-fdd6-5d20-a6ef-dddc3ac70419"
)


def canonical_json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def attempt_id_for(visit_id: UUID, attempt_index: int) -> UUID:
    if attempt_index < 0:
        raise ValueError("attempt_index must be non-negative")
    return uuid5(_ATTEMPT_NAMESPACE, f"{visit_id}:{attempt_index}")


def document_id_for(visit_id: UUID) -> UUID:
    return uuid5(_DOCUMENT_NAMESPACE, str(visit_id))


def link_id_for(source_url: str, target_url: str) -> UUID:
    """Return the versioned identity of one directed normalized page pair."""

    if not source_url or not target_url:
        raise ValueError("link URLs must not be empty")
    return uuid5(
        _LINK_NAMESPACE,
        "v1:" + canonical_json([source_url, target_url]),
    )


def link_occurrence_id_for(
    document_id: UUID, element_index: int
) -> UUID:
    if element_index < 0:
        raise ValueError("element_index must be non-negative")
    return uuid5(
        _LINK_OCCURRENCE_NAMESPACE,
        f"v1:{document_id}:{element_index}",
    )


class CatalogueRecord(BaseModel):
    model_config = ConfigDict(frozen=True)


class PeriplusProvenance(CatalogueRecord):
    kind: Literal["periplus"] = "periplus"


class ExternalProvenance(CatalogueRecord):
    kind: Literal["external"] = "external"
    system: str = Field(min_length=1, max_length=256)
    dataset: str | None = Field(default=None, min_length=1, max_length=512)
    source_record_id: str = Field(min_length=1, max_length=2048)


EvidenceProvenance = Annotated[
    PeriplusProvenance | ExternalProvenance,
    Field(discriminator="kind"),
]


class VisitRecord(CatalogueRecord):
    model_config = ConfigDict(frozen=True, extra="forbid")

    visit_id: UUID
    visibility: Literal["public", "private"] = "public"
    requested_url: str = Field(min_length=1)
    effective_url: str | None = Field(default=None, min_length=1)
    admitted_at: datetime
    started_at: datetime | None = None
    observed_at: datetime | None = None
    finished_at: datetime
    outcome: Literal["succeeded", "failed", "cancelled", "skipped"]
    status_code: int | None = Field(default=None, ge=100, le=599)
    document_id: UUID | None = None
    provenance: EvidenceProvenance = Field(
        default_factory=PeriplusProvenance,
        discriminator="kind",
    )

    @model_validator(mode="after")
    def validate_record(self) -> VisitRecord:
        if (
            self.provenance.kind == "periplus"
            and self.started_at is not None
            and self.started_at < self.admitted_at
        ):
            raise ValueError("visit started_at cannot precede admitted_at")
        if (
            self.provenance.kind == "periplus"
            and self.finished_at < (self.started_at or self.admitted_at)
        ):
            raise ValueError("visit finished_at precedes its start")
        if self.observed_at is not None:
            if self.provenance.kind == "periplus" and self.started_at is None:
                raise ValueError("an observed visit must have started")
            if (
                self.provenance.kind == "periplus"
                and self.started_at is not None
                and not self.started_at <= self.observed_at <= self.finished_at
            ):
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


class AttemptUsage(CatalogueRecord):
    """Client capture time, not a provider billing or remote-execution guarantee."""
    policy_version: int = Field(ge=1)
    domain_policy: DomainPolicySnapshot | None = None
    exclusion_policy_version: int | None = Field(default=None, ge=1)
    reserved_ms: int = Field(ge=1)
    measured_ms: int | None = Field(default=None, ge=0)

    @property
    def charged_ms(self) -> int:
        return self.reserved_ms if self.measured_ms is None else self.measured_ms


class AttemptRecord(CatalogueRecord):
    resource_usage: AttemptUsage | None = None
    attempt_id: UUID
    visit_id: UUID
    attempt_index: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime | None
    effective_url: str | None = Field(default=None, min_length=1)
    status_code: int | None = Field(default=None, ge=100, le=599)
    outcome: Literal["succeeded", "failed", "cancelled", "uncertain"]
    failure_stage: str | None = Field(default=None, max_length=128)
    failure_code: str | None = Field(default=None, max_length=128)
    failure_message: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_record(self) -> AttemptRecord:
        if self.attempt_id != attempt_id_for(self.visit_id, self.attempt_index):
            raise ValueError("attempt_id must be derived from visit_id and attempt_index")
        if self.finished_at is None and self.outcome != "uncertain":
            raise ValueError("only uncertain attempts may have no known finish time")
        if self.finished_at is not None and self.finished_at < self.started_at:
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
    attempt_id: UUID | None = None
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
            if self.visit.provenance.kind == "periplus":
                if self.document.attempt_id not in attempt_ids:
                    raise ValueError("document attempt is absent from visit evidence")
                successful = next(
                    attempt
                    for attempt in self.attempts
                    if attempt.attempt_id == self.document.attempt_id
                )
                if successful.outcome != "succeeded":
                    raise ValueError("document attempt must have succeeded")
            elif self.document.attempt_id is not None:
                if self.document.attempt_id not in attempt_ids:
                    raise ValueError("document attempt is absent from visit evidence")
        grouped: dict[UUID, list[int]] = {}
        for step in self.steps:
            if step.attempt_id not in attempt_ids:
                raise ValueError("step belongs to an absent attempt")
            grouped.setdefault(step.attempt_id, []).append(step.step_index)
        if any(indexes != list(range(len(indexes))) for indexes in grouped.values()):
            raise ValueError("step indexes must be contiguous from zero per attempt")
        return self


class IngestionWriteResult(CatalogueRecord):
    kind: Literal["visit", "lineage"]
    identity: UUID
    created: bool
    repository_snapshot: int = Field(ge=0)
