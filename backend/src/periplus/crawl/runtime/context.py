from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class GraphExecutionContext:
    """Frozen URL-level provenance supplied by the graph runtime."""

    graph_id: UUID
    graph_run_id: UUID
    graph_node_id: UUID
    crawl_request_id: UUID
    admitted_at: datetime
    effective_policy_snapshot_json: dict
    prior_attempts_json: tuple[dict, ...] = ()
    source_crawl_id: UUID | None = None
    source_edge_id: UUID | None = None


_current: ContextVar[GraphExecutionContext | None] = ContextVar(
    "graph_execution", default=None
)


def current_graph_execution() -> GraphExecutionContext | None:
    return _current.get()


def commit_checkpoint(session: Session | None) -> None:
    if session is not None:
        session.commit()


@contextmanager
def graph_execution_scope(context: GraphExecutionContext):
    token = _current.set(context)
    try:
        yield
    finally:
        _current.reset(token)
