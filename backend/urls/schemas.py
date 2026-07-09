from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from artifacts.schemas import ArtifactListRecord
from crawls.schemas import CrawlListRecord


class UrlListRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    normalized_url: str
    scheme: str
    host: str
    domain: str
    path: str
    query_fingerprint: str | None = None
    crawl_count: int = 0
    artifact_count: int = 0
    active_artifact_count: int = 0
    invalidated_artifact_count: int = 0
    cache_eligible_count: int = 0
    latest_status_code: int | None = None
    latest_crawl_at: datetime | None = None
    latest_artifact_at: datetime | None = None
    warning_count: int = 0
    crawl_warning_count: int = 0
    artifact_warning_count: int = 0


class UrlListResponse(BaseModel):
    items: list[UrlListRecord]
    total: int
    limit: int
    offset: int


class UrlDetailRecord(UrlListRecord):
    recent_crawls: list[CrawlListRecord]
    artifacts: list[ArtifactListRecord]
