from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from .service import enqueue_due_task_runs


def run_scheduler_once(session_factory: sessionmaker, limit: int = 20) -> int:
    with session_factory() as session:
        enqueued = enqueue_due_task_runs(session=session, limit=limit)
        session.commit()
        return enqueued
