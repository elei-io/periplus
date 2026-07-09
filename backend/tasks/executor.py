from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from actions.extract.service import extract
from actions.index.service import index
from actions.crawl.service import crawl
from actions.search.service import search
from actions.shared.data_schema.service import schema
from actions.shared.progress import CrawlProgressCallback

from .models import Task, TaskRun

_LEASE_SECONDS = 30 * 60


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_safe(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[no-any-return]
    return json.loads(json.dumps(value, default=str))


@dataclass
class PrimitiveExecution:
    output_json: dict
    response_json: object
    warnings: list[dict]


async def _execute_primitive(
    task: Task,
    session: Session,
    run: TaskRun,
    progress_callback: CrawlProgressCallback | None = None,
) -> PrimitiveExecution:
    primitive = task.primitive
    payload = task.input_json

    if primitive == "search":
        results = await search(
            **payload,
            progress_callback=progress_callback,
            session=session,
            task_run_id=run.id,
        )
        response_json = [_json_safe(result) for result in results]
        return PrimitiveExecution(
            output_json={"results": response_json},
            response_json=response_json,
            warnings=[],
        )

    if primitive == "index":
        links = await index(
            **payload,
            progress_callback=progress_callback,
            session=session,
            task_run_id=run.id,
        )
        response_json = [_json_safe(link) for link in links]
        return PrimitiveExecution(
            output_json={"links": response_json},
            response_json=response_json,
            warnings=[],
        )

    if primitive == "crawl":
        raise RuntimeError("crawl primitive requires task-run execution context")
    if primitive == "schema":
        output = await schema(
            **payload,
            progress_callback=progress_callback,
            session=session,
            task_run_id=run.id,
        )
        response_json = _json_safe(output)
        return PrimitiveExecution(
            output_json=response_json,
            response_json=response_json,
            warnings=[],
        )

    if primitive == "extract":
        output = await extract(
            **payload,
            progress_callback=progress_callback,
            session=session,
            task_run_id=run.id,
        )
        warnings = [_json_safe(warning) for warning in output.warnings]
        response_json = _json_safe(output)
        return PrimitiveExecution(
            output_json=response_json,
            response_json=response_json,
            warnings=warnings,
        )

    raise ValueError(f"Unsupported task primitive: {primitive}")


async def _execute_crawl_primitive(
    session: Session,
    run: TaskRun,
    progress_callback: CrawlProgressCallback | None = None,
) -> PrimitiveExecution:
    output = await crawl(
        **run.task.input_json,
        progress_callback=progress_callback,
        session=session,
        task_run_id=run.id,
    )
    warnings: list[dict] = []
    pages = []
    for page in output.pages:
        page_warnings = [_json_safe(warning) for warning in page.warnings]
        warnings.extend(page_warnings)
        pages.append(
            {
                "url": page.url,
                "success": page.success,
                "status_code": page.status_code,
                "duration_seconds": page.duration_seconds,
                "error": page.error,
                "crawl_id": str(page.crawl_id) if page.crawl_id else None,
                "artifact_ids": [str(artifact_id) for artifact_id in page.artifact_ids],
            }
        )

    return PrimitiveExecution(
        output_json={
            "stats": _json_safe(output.stats),
            "pages": pages,
        },
        response_json=_json_safe(output),
        warnings=warnings,
    )


def claim_next_run(session: Session, worker_id: str, lease_seconds: int = _LEASE_SECONDS) -> TaskRun | None:
    now = _utc_now()
    statement = (
        select(TaskRun)
        .where(TaskRun.status == "queued")
        .order_by(TaskRun.queued_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    run = session.scalars(statement).first()
    if run is None:
        return None

    run.status = "running"
    run.leased_by = worker_id
    run.leased_at = now
    run.leased_until = now + timedelta(seconds=lease_seconds)
    run.started_at = now
    session.flush()
    return run


async def execute_run(
    session: Session,
    run_id: UUID,
    progress_callback: CrawlProgressCallback | None = None,
) -> object | None:
    run = session.get(TaskRun, run_id)
    if run is None:
        return None

    task = run.task
    output_json: dict | None = None
    response_json: object | None = None
    warnings: list[dict] = []

    try:
        if task.primitive == "crawl":
            execution = await _execute_crawl_primitive(
                session=session,
                run=run,
                progress_callback=progress_callback,
            )
        else:
            execution = await _execute_primitive(
                task,
                session=session,
                run=run,
                progress_callback=progress_callback,
            )
        output_json = execution.output_json
        response_json = execution.response_json
        warnings = execution.warnings

        warnings_json = {
            "codes": [warning.get("code") for warning in warnings if warning.get("code")],
            "count": len(warnings),
            "path": None,
            "warnings": warnings,
        }

        run.status = "succeeded"
        run.output_json = output_json
        run.warnings_json = warnings_json
        run.error = None
        task.last_run_at = _utc_now()
    except Exception as exc:
        error = str(exc)
        session.rollback()
        run = session.get(TaskRun, run_id)
        if run is None:
            raise
        task = run.task
        warnings_json = {
            "codes": [],
            "count": 0,
            "path": None,
            "warnings": [],
        }
        run.status = "failed"
        run.error = error
        run.warnings_json = warnings_json
    finally:
        now = _utc_now()
        run.finished_at = now
        run.leased_until = None
        task.updated_at = now
        session.flush()

    return response_json


async def run_worker_once(session_factory, worker_id: str | None = None) -> bool:
    worker_id = worker_id or f"{os.uname().nodename}:{os.getpid()}"
    with session_factory() as session:
        run = claim_next_run(session=session, worker_id=worker_id)
        if run is None:
            return False
        run_id = run.id
        session.commit()

    with session_factory() as session:
        await execute_run(session=session, run_id=run_id)
        session.commit()

    return True


def run_worker_once_sync(session_factory, worker_id: str | None = None) -> bool:
    return asyncio.run(run_worker_once(session_factory=session_factory, worker_id=worker_id))
