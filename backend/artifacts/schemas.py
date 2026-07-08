from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

ArtifactKind = Literal["html", "screenshot", "pdf", "mhtml", "crawl.json", "result.json", "atlas.json"]


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
    extracted: dict[str, Any]
    meta: dict[str, Any]
    warnings_json: dict[str, Any]
    invalidated_at: datetime | None = None
    invalidated_reason: str | None = None
    created_at: datetime
