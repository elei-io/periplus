from dataclasses import dataclass
from typing import Any, BinaryIO
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from repository.objects.artifact import ArtifactIdentity


@dataclass(slots=True)
class CapturedArtifact:
    content: BinaryIO
    identity: ArtifactIdentity
    media_type: str
    filename: str | None = None

    def close(self) -> None:
        self.content.close()


class CrawlPage(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    url: str
    success: bool
    status_code: int | None = None
    duration_seconds: float
    crawl_id: UUID | None = None
    document_id: str | None = None
    artifact_id: str | None = None
    repository_snapshot: int | None = None
    repository_crawl_created: bool | None = None
    html: str | None = None
    artifact: CapturedArtifact | None = None
    crawl: dict[str, Any] | None = None
    error: str | None = None
    failure_code: str | None = None
    failure_stage: str | None = None
    failure_retryable: bool | None = None
    retry_after_seconds: float | None = None
