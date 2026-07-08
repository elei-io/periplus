from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from actions.extract.service import extract
from actions.index.service import index
from actions.scrape.service import scrape
from actions.search.service import search
from actions.shared.extract_schema.service import schema
from actions.shared.progress import CrawlProgressCallback
from artifacts.models import Artifact
from artifacts.service import task_run_artifacts_dir

from .models import Task, TaskRun, TaskRunArtifact

_LEASE_SECONDS = 30 * 60


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_safe(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[no-any-return]
    return json.loads(json.dumps(value, default=str))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(value), indent=2, sort_keys=True), encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _create_artifact(
    session: Session,
    run: TaskRun,
    kind: str,
    path: Path,
    meta: dict,
) -> Artifact:
    content = path.read_bytes()
    artifact = Artifact(
        task_run_id=run.id,
        kind=kind,
        path=str(path),
        content_type=_content_type_for_kind(kind),
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        extracted={},
        meta=meta,
        warnings_json={},
    )
    session.add(artifact)
    session.flush()
    session.add(TaskRunArtifact(task_run_id=run.id, artifact_id=artifact.id, role="produced"))
    session.flush()
    return artifact


def _content_type_for_kind(kind: str) -> str:
    if kind == "html":
        return "text/html"
    if kind.endswith(".json"):
        return "application/json"
    if kind == "pdf":
        return "application/pdf"
    if kind == "mhtml":
        return "multipart/related"
    return "application/octet-stream"


@dataclass
class PrimitiveExecution:
    output_json: dict
    response_json: object
    warnings: list[dict]
    artifacts: list[tuple[str, str, object]]


async def _execute_primitive(
    task: Task,
    progress_callback: CrawlProgressCallback | None = None,
) -> PrimitiveExecution:
    primitive = task.primitive
    payload = task.input_json

    if primitive == "search":
        results = await search(**payload, progress_callback=progress_callback)
        response_json = [_json_safe(result) for result in results]
        return PrimitiveExecution(
            output_json={"results": response_json},
            response_json=response_json,
            warnings=[],
            artifacts=[],
        )

    if primitive == "index":
        links = await index(**payload, progress_callback=progress_callback)
        response_json = [_json_safe(link) for link in links]
        return PrimitiveExecution(
            output_json={"links": response_json},
            response_json=response_json,
            warnings=[],
            artifacts=[],
        )

    if primitive == "scrape":
        output = await scrape(**payload, progress_callback=progress_callback)
        warnings: list[dict] = []
        page_artifacts: list[tuple[str, str, object]] = []
        pages = []
        for index_, page in enumerate(output.pages):
            page_warnings = [_json_safe(warning) for warning in page.warnings]
            warnings.extend(page_warnings)
            page_dir = f"pages/{index_:04d}"
            if page.html is not None:
                page_artifacts.append(("html", f"{page_dir}/result.html", page.html))
            if page.crawl is not None:
                page_artifacts.append(("crawl.json", f"{page_dir}/result.json", page.crawl))
            pages.append(
                {
                    "url": page.url,
                    "success": page.success,
                    "status_code": page.status_code,
                    "duration_seconds": page.duration_seconds,
                    "error": page.error,
                    "artifact_ids": [],
                }
            )

        return PrimitiveExecution(
            output_json={
                "stats": _json_safe(output.stats),
                "pages": pages,
            },
            response_json=_json_safe(output),
            warnings=warnings,
            artifacts=page_artifacts,
        )

    if primitive == "schema":
        output = await schema(**payload, progress_callback=progress_callback)
        response_json = _json_safe(output)
        return PrimitiveExecution(
            output_json=response_json,
            response_json=response_json,
            warnings=[],
            artifacts=[],
        )

    if primitive == "extract":
        output = await extract(**payload, progress_callback=progress_callback)
        warnings = [_json_safe(warning) for warning in output.warnings]
        response_json = _json_safe(output)
        return PrimitiveExecution(
            output_json=response_json,
            response_json=response_json,
            warnings=warnings,
            artifacts=[],
        )

    raise ValueError(f"Unsupported task primitive: {primitive}")


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
    run_dir = task_run_artifacts_dir(run.id)

    try:
        execution = await _execute_primitive(task, progress_callback=progress_callback)
        output_json = execution.output_json
        response_json = execution.response_json
        warnings = execution.warnings
        extra_artifacts = execution.artifacts

        result_path = run_dir / "result.json"
        atlas_path = run_dir / "atlas.json"
        _write_json(result_path, output_json)

        for kind, relative_path, content in extra_artifacts:
            artifact_path = run_dir / relative_path
            if kind == "html":
                _write_text(artifact_path, str(content))
            else:
                _write_json(artifact_path, content)
            _create_artifact(
                session=session,
                run=run,
                kind=kind,
                path=artifact_path,
                meta={"primitive": task.primitive, "task_id": str(task.id)},
            )

        warnings_json = {
            "codes": [warning.get("code") for warning in warnings if warning.get("code")],
            "count": len(warnings),
            "path": str(atlas_path),
            "warnings": warnings,
        }
        _write_json(atlas_path, {"warnings": warnings_json})
        _create_artifact(
            session=session,
            run=run,
            kind="result.json",
            path=result_path,
            meta={"primitive": task.primitive, "task_id": str(task.id)},
        )
        _create_artifact(
            session=session,
            run=run,
            kind="atlas.json",
            path=atlas_path,
            meta={"primitive": task.primitive, "task_id": str(task.id)},
        )

        run.status = "succeeded"
        run.output_json = output_json
        run.warnings_json = warnings_json
        run.error = None
        task.last_run_at = _utc_now()
    except Exception as exc:
        atlas_path = run_dir / "atlas.json"
        warnings_json = {
            "codes": [],
            "count": 0,
            "path": str(atlas_path),
            "warnings": [],
        }
        _write_json(atlas_path, {"warnings": warnings_json, "error": str(exc)})
        _create_artifact(
            session=session,
            run=run,
            kind="atlas.json",
            path=atlas_path,
            meta={"primitive": task.primitive, "task_id": str(task.id)},
        )
        run.status = "failed"
        run.error = str(exc)
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
