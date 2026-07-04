from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

ArtifactKind = Literal["result.html", "result.json", "atlas.json"]


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_run_id: UUID
    kind: ArtifactKind
    path: str
    cache_key: str | None = None
    meta: dict[str, Any]
    created_at: datetime
