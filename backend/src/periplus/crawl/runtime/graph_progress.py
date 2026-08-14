"""Read models for progress derived from authoritative Postgres rows."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NodeActivity(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: UUID
    status: str
    url: str
    updated_at: datetime
    error: str | None


class NodeProgress(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["node_progress"] = "node_progress"
    graph_run_id: UUID
    node_id: UUID
    admitted: int
    queued: int
    crawling: int
    awaiting_navigation: int
    evaluating_edges: int
    completed: int
    failed: int
    cancelled: int
    activity: tuple[NodeActivity, ...]
    settled: bool


class EdgeProgress(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["edge_progress"] = "edge_progress"
    graph_run_id: UUID
    edge_id: UUID
    evaluations_pending: int
    evaluations_running: int
    evaluations_completed: int
    evaluations_failed: int
    urls_selected: int
    urls_admitted: int
    urls_deduplicated: int
    settled: bool
