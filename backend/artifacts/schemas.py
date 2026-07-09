from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ArtifactKind = Literal["html", "screenshot", "pdf", "mhtml"]


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    crawl_id: UUID | None = None
    url_id: UUID | None = None
    task_run_id: UUID | None = None
    kind: ArtifactKind
    path: str
    content_type: str
    size_bytes: int
    sha256: str
    input_hash: str | None = None
    meta: dict[str, Any]
    warnings_json: dict[str, Any]
    invalidated_at: datetime | None = None
    invalidated_reason: str | None = None
    created_at: datetime


class ArtifactListRecord(BaseModel):
    id: UUID
    crawl_id: UUID | None = None
    url_id: UUID | None = None
    task_run_id: UUID | None = None
    kind: ArtifactKind
    path: str
    content_type: str
    size_bytes: int
    sha256: str
    input_hash: str | None = None
    warning_count: int = 0
    invalidated_at: datetime | None = None
    invalidated_reason: str | None = None
    created_at: datetime
    url: str | None = None
    normalized_url: str | None = None
    domain: str | None = None
    path_name: str | None = None


class ArtifactListResponse(BaseModel):
    items: list[ArtifactListRecord]
    total: int
    limit: int
    offset: int


class ArtifactDetailRecord(ArtifactRecord):
    warning_count: int = 0
    url: str | None = None
    normalized_url: str | None = None
    domain: str | None = None
    path_name: str | None = None


class ArtifactInvalidateRequest(BaseModel):
    artifact_ids: list[UUID] | None = Field(default=None, min_length=1)
    url_ids: list[UUID] | None = Field(default=None, min_length=1)
    url_pattern: str | None = None
    kind: str | None = None
    warnings: bool | None = None
    reason: str = "manual"

    @model_validator(mode="after")
    def require_target(self) -> "ArtifactInvalidateRequest":
        if not self.artifact_ids and not self.url_ids and not self.url_pattern and not self.kind and self.warnings is None:
            raise ValueError("artifact_ids, url_ids, or filters are required")
        return self


class ArtifactInvalidateResponse(BaseModel):
    invalidated: int


class ArtifactInvalidateExpiredRequest(BaseModel):
    max_age_seconds: int | None = Field(default=None, ge=1)
    reason: str = "ttl"


class ArtifactCleanupRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000)


class ArtifactCleanupResponse(BaseModel):
    rows_deleted: int
    files_deleted: int
    missing_files: int
    errors: int
