from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class TaskExecutionContext:
    run_id: UUID
    attempt: int
    task_id: UUID
    task_revision: int
    primitive: str
    data_schema_id: UUID | None
    crawl_policy_snapshots_json: list[dict]


_current: ContextVar[TaskExecutionContext | None] = ContextVar("task_execution", default=None)


def current_task_execution() -> TaskExecutionContext | None:
    return _current.get()


def commit_task_checkpoint(session: Session) -> None:
    session.commit()


@contextmanager
def task_execution_scope(context: TaskExecutionContext):
    token = _current.set(context)
    try:
        yield
    finally:
        _current.reset(token)
