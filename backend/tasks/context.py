from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class TaskExecutionContext:
    run_id: UUID
    attempt: int
    lease_token: UUID


_current: ContextVar[TaskExecutionContext | None] = ContextVar("task_execution", default=None)


def current_task_execution() -> TaskExecutionContext | None:
    return _current.get()


def commit_task_checkpoint(session: Session) -> None:
    context = current_task_execution()
    if context is None:
        session.commit()
        return

    from tasks.models import TaskRun, TaskRunLease

    lease = session.scalar(
        select(TaskRunLease)
        .where(
            TaskRunLease.run_id == context.run_id,
            TaskRunLease.lease_token == context.lease_token,
            TaskRunLease.attempt == context.attempt,
            TaskRunLease.expires_at > datetime.now(UTC),
        )
        .with_for_update()
    )
    run = session.get(TaskRun, context.run_id)
    if lease is None or run is None or run.status != "running":
        session.rollback()
        raise RuntimeError(f"Task run {context.run_id} lost ownership before checkpoint commit.")
    session.commit()


@contextmanager
def task_execution_scope(context: TaskExecutionContext):
    token = _current.set(context)
    try:
        yield
    finally:
        _current.reset(token)
