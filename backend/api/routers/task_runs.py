import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from actions.shared.nats_progress import nats_available, stream_progress
from db.session import SessionLocal, get_session
from tasks.schemas import TaskOperationsRecord, TaskPrimitive, TaskRunRecord
from tasks.service import (
    TaskNotFoundError,
    TaskRunConflictError,
    get_task_run,
    get_task_run_result,
    list_recent_task_runs,
    request_task_run_cancellation,
    task_operations,
)

router = APIRouter(prefix="/task-runs", tags=["task-runs"])
_TERMINAL = {"succeeded", "failed", "cancelled", "skipped"}


def _sse(event: str, data: object, event_id: str | None = None) -> str:
    lines = [f"event: {event}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {json.dumps(data)}")
    return "\n".join(lines) + "\n\n"


def _terminal_envelope(run: TaskRunRecord, cursor: tuple[int, int]) -> dict[str, object]:
    attempt = run.attempt
    sequence = cursor[1] + 1 if cursor[0] == attempt else 1
    event_id = f"{attempt}:{sequence}"
    return {
        "event_id": event_id,
        "run_id": str(run.id),
        "attempt": attempt,
        "sequence": sequence,
        "type": run.status,
        "timestamp": datetime.now(UTC).isoformat(),
        "data": {"status": run.status, "error": run.error},
    }


def _read_run(run_id: UUID) -> TaskRunRecord:
    with SessionLocal() as session:
        return get_task_run(session, run_id)


def _terminal_sse(run: TaskRunRecord, cursor: tuple[int, int]) -> str:
    envelope = _terminal_envelope(run, cursor)
    return _sse(run.status, envelope, str(envelope["event_id"]))


def _lifecycle_sse(run: TaskRunRecord, phase: str) -> tuple[str, tuple[int, int]]:
    if phase == "queue":
        cursor = (0, 1)
        status = "waiting"
        message = "Waiting for a worker."
        event_phase = "queue"
    elif phase == "queue_complete":
        cursor = (0, 2)
        status = "succeeded"
        message = "Worker claimed the task."
        event_phase = "queue"
    else:
        cursor = (run.attempt, 0)
        status = "started"
        message = "Task started."
        event_phase = "task"
    event_id = f"{cursor[0]}:{cursor[1]}"
    envelope = {
        "event_id": event_id,
        "run_id": str(run.id),
        "attempt": cursor[0],
        "sequence": cursor[1],
        "type": "progress",
        "timestamp": datetime.now(UTC).isoformat(),
        "data": {
            "operation_id": f"{run.id}:{event_phase}:{0 if event_phase == 'queue' else run.attempt}",
            "phase": event_phase,
            "status": status,
            "message": message,
            "metadata": {"attempt": run.attempt},
        },
    }
    return _sse("progress", envelope, event_id), cursor


async def _poll_until_terminal(run_id: UUID, cursor: tuple[int, int]) -> AsyncIterator[str]:
    heartbeat_seconds = 15
    elapsed = 0
    while True:
        run = _read_run(run_id)
        if run.status in _TERMINAL:
            yield _terminal_sse(run, cursor)
            return
        await asyncio.sleep(1)
        elapsed += 1
        if elapsed >= heartbeat_seconds:
            elapsed = 0
            yield ": heartbeat\n\n"


@router.get("/operations/summary", response_model=TaskOperationsRecord)
async def operations() -> TaskOperationsRecord:
    with SessionLocal() as session:
        summary = task_operations(session)
    summary.nats_available = await nats_available()
    return summary


@router.get("/", response_model=list[TaskRunRecord])
def list_(
    session: Annotated[Session, Depends(get_session)],
    primitive: TaskPrimitive,
    terminal_limit: int = 3,
) -> list[TaskRunRecord]:
    if terminal_limit < 0 or terminal_limit > 20:
        raise HTTPException(status_code=422, detail="terminal_limit must be between 0 and 20.")
    return list_recent_task_runs(session, primitive=primitive, terminal_limit=terminal_limit)


@router.get("/{run_id}", response_model=TaskRunRecord)
def get(run_id: UUID, session: Annotated[Session, Depends(get_session)]) -> TaskRunRecord:
    try:
        return get_task_run(session, run_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{run_id}/cancel", response_model=TaskRunRecord)
def cancel(run_id: UUID, session: Annotated[Session, Depends(get_session)]) -> TaskRunRecord:
    try:
        return request_task_run_cancellation(session, run_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{run_id}/result", response_model=None)
def result(run_id: UUID, session: Annotated[Session, Depends(get_session)]) -> Any:
    try:
        return get_task_run_result(session, run_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskRunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _progress_stream(run_id: UUID, after_event_id: tuple[int, int]) -> AsyncIterator[str]:
    run = _read_run(run_id)
    if run.status in _TERMINAL:
        yield _terminal_sse(run, after_event_id)
        return

    if run.status == "queued" and after_event_id < (0, 1):
        event, after_event_id = _lifecycle_sse(run, "queue")
        yield event
    elif run.status == "running" and after_event_id < (run.attempt, 0):
        event, after_event_id = _lifecycle_sse(run, "task")
        yield event

    try:
        async for envelope in stream_progress(
            run_id,
            current_attempt=run.attempt,
            after_event_id=after_event_id,
        ):
            if envelope["type"] == "heartbeat":
                run = _read_run(run_id)
                if run.status in _TERMINAL:
                    yield _terminal_sse(run, after_event_id)
                    return
                if run.status == "running" and after_event_id < (run.attempt, 0):
                    if after_event_id < (0, 2):
                        event, after_event_id = _lifecycle_sse(run, "queue_complete")
                        yield event
                    event, after_event_id = _lifecycle_sse(run, "task")
                    yield event
                yield ": heartbeat\n\n"
                continue
            after_event_id = (int(envelope["attempt"]), int(envelope["sequence"]))
            yield _sse(envelope["type"], envelope, str(envelope["event_id"]))
    except Exception as exc:
        print(f"atlas-api progress stream unavailable for run {run_id}: {exc}", flush=True)
        async for event in _poll_until_terminal(run_id, after_event_id):
            yield event


@router.get("/{run_id}/progress")
def progress(
    run_id: UUID,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    try:
        _read_run(run_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        attempt, sequence = (last_event_id or "0:0").split(":", maxsplit=1)
        after_event_id = (int(attempt), int(sequence))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Last-Event-ID must use attempt:sequence.") from exc
    return StreamingResponse(
        _progress_stream(run_id, after_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
