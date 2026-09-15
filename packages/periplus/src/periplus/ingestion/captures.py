"""Archive-owned observations, independent of operational jobs and databases."""

from datetime import datetime
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from periplus.crawl.acquisition.records import VisitEvidence
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from periplus.ingestion.archive_source import ArchiveSource


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


class Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    content_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0)
    object_key: str = Field(min_length=1, max_length=2048)
    storage_encoding: Literal["zstd", "identity"]
    stored_bytes: int = Field(ge=0)
    representation: Literal["response_body", "rendered_html"]
    media_type: str = Field(min_length=1, max_length=512)
    declared_media_type: str | None = None
    charset: str | None = None

    @property
    def document_id(self) -> str:
        return sha256(
            canonical(
                [
                    self.content_id,
                    self.representation,
                    self.media_type,
                    (self.charset or "utf-8").lower(),
                ]
            )
        ).hexdigest()


class Capture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    format_version: Literal[1] = 1
    capture_id: UUID
    requested_url: str = Field(min_length=1, max_length=8192)
    effective_url: str | None = Field(default=None, max_length=8192)
    captured_at: AwareDatetime | None = None
    timestamp_precision: Literal["microsecond", "second", "unknown"] = "unknown"
    http_status: int | None = Field(default=None, ge=100, le=599)
    completeness: Literal["complete", "unavailable"]
    payload: Payload | None = None
    source: ArchiveSource | None = None

    @model_validator(mode="after")
    def validate_evidence(self):
        if (self.completeness == "complete") != (self.payload is not None):
            raise ValueError("Complete captures require a payload")
        if (self.captured_at is None) != (self.timestamp_precision == "unknown"):
            raise ValueError("Capture timestamp and precision disagree")
        if self.source and self.source.capture_id != self.capture_id:
            raise ValueError("Capture identity differs from archive provenance")
        return self

    @property
    def digest(self) -> str:
        return sha256(canonical(self.model_dump(mode="json"))).hexdigest()


def from_visit(evidence: "VisitEvidence") -> Capture:
    """The native acquisition boundary deliberately drops operational context."""
    visit, document = evidence.visit, evidence.document
    return Capture(
        capture_id=visit.visit_id,
        requested_url=visit.requested_url,
        effective_url=visit.effective_url,
        captured_at=visit.observed_at,
        timestamp_precision="second"
        if visit.archive_source
        else "microsecond"
        if visit.observed_at
        else "unknown",
        http_status=visit.status_code,
        completeness="complete" if document else "unavailable",
        source=visit.archive_source,
        payload=Payload(
            content_id=document.content_sha256,
            byte_length=document.content_bytes,
            object_key=document.object_key,
            storage_encoding=document.storage_encoding,
            stored_bytes=document.stored_bytes,
            representation=document.representation,
            media_type=document.detected_media_type,
            declared_media_type=document.declared_media_type,
            charset=document.charset,
        )
        if document
        else None,
    )
