from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CrawlPolicyRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url_match_id: UUID | None = None
    match: str
    enabled: bool
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CrawlPolicyListRecord(CrawlPolicyRecord):
    template: str | None = None
    mode: str | None = None
    wait: str | None = None
    max_concurrency: int | None = None


class CrawlPolicyListResponse(BaseModel):
    items: list[CrawlPolicyListRecord]
    total: int
    limit: int
    offset: int


class CrawlPolicyUpdateRequest(BaseModel):
    enabled: bool | None = None
    match: str | None = None
    config: dict[str, Any] | None = None
