"""Bounded API records and pagination contracts."""

import base64
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field
from periplus.crawl.control.collections.schemas import CollectionExecutionSpec


class ObservationReadiness(BaseModel):
    observation_id: UUID
    query_ready: bool | None
    reason: str
    generation_id: UUID | None = None
    as_of: datetime


class CollectionReadiness(BaseModel):
    collection_id: UUID
    query_ready: bool | None
    reason: str
    generation_id: UUID | None = None
    as_of: datetime
