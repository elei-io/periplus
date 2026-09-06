"""Frozen work contracts crossing the materialization queue."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class LiveBatchWork(BaseModel):
    """One frozen, replayable active-generation visit batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["live"] = "live"
    batch_id: UUID
    generation_id: UUID
    ordinal: int
    snapshot: int
    visit_ids: tuple[str, ...]
