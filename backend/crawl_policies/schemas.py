from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CrawlPolicyRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    match: str
    enabled: bool
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime
