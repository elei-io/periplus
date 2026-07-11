"""NATS-backed current task-run behavior."""

import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from control.crawl_policies.service import snapshot_enabled_crawl_policies
from control.tasks.models import Task
from control.tasks.schemas import TaskPrimitive
from control.tasks.service import (
    TaskNotFoundError,
    get_or_create_ad_hoc_task,
    next_run_after_enqueue,
)

from .task_queue import (
    TaskRunState,
    TaskRunStatus,
    TaskRunTriggerKind,
    WorkerState,
    connect_nats,
    create_run,
    ensure_task_storage,
    get_run,
    list_runs,
    new_run,
    publish_run,
    release_task,
    reserve_task,
    update_run,
)

_ACTIVE_STATUSES = ("queued", "running")


class TaskRunConflictError(Exception):
    pass


class TaskRunRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    task_revision: int
    primitive: TaskPrimitive
    status: TaskRunStatus
    trigger_kind: TaskRunTriggerKind
    data_schema_id: UUID | None = None
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancellation_requested_at: datetime | None = None
    cancelled_at: datetime | None = None
    attempt: int
    failed_attempts: int
    max_attempts: int
    input_json: dict
    warnings_json: dict
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskRunSubmission(BaseModel):
    task_id: UUID
    run_id: UUID
    status: Literal["queued"] = "queued"


class TaskOperationsRecord(BaseModel):
    queued: int
    running: int
    cancelling: int
    stale_workers: int
    oldest_queued_at: datetime | None = None
    average_queue_latency_seconds: float | None = None
    average_execution_seconds: float | None = None
    nats_available: bool | None = None
    workers: list[WorkerState]


def _record(run: TaskRunState) -> TaskRunRecord:
    return TaskRunRecord.model_validate(run)


async def _storage():
    client = await connect_nats()
    jetstream = client.jetstream()
    runs, workers = await ensure_task_storage(jetstream)
    return client, jetstream, runs, workers


async def all_task_runs() -> list[TaskRunState]:
    client, _jetstream, runs, _workers = await _storage()
    try:
        return await list_runs(runs)
    finally:
        await client.drain()


async def list_task_runs(
    session: Session,
    task_id: UUID,
    limit: int = 100,
    offset: int = 0,
) -> list[TaskRunRecord]:
    if session.get(Task, task_id) is None:
        raise TaskNotFoundError(f"Task {task_id} was not found.")
    values = [run for run in await all_task_runs() if run.task_id == task_id]
    values.sort(key=lambda run: run.queued_at, reverse=True)
    return [_record(run) for run in values[offset : offset + limit]]


async def list_recent_task_runs(
    primitive: TaskPrimitive,
    terminal_limit: int = 3,
) -> list[TaskRunRecord]:
    values = [run for run in await all_task_runs() if run.primitive == primitive]
    active = [run for run in values if run.status in _ACTIVE_STATUSES]
    active.sort(key=lambda run: run.queued_at, reverse=True)
    terminal = [run for run in values if run.status not in _ACTIVE_STATUSES]
    terminal.sort(key=lambda run: run.finished_at or run.queued_at, reverse=True)
    return [_record(run) for run in (*active, *terminal[:terminal_limit])]


async def enqueue_task_run(
    session: Session,
    task: Task,
    trigger_kind: TaskRunTriggerKind,
    now: datetime | None = None,
) -> TaskRunState | None:
    client, jetstream, runs, _workers = await _storage()
    try:
        now = now or datetime.now(UTC)
        run = new_run(
            task_id=task.id, task_revision=task.revision, primitive=task.primitive,
            trigger_kind=trigger_kind, input_json=json.loads(json.dumps(task.input_json)),
            crawl_policy_snapshots_json=snapshot_enabled_crawl_policies(session), now=now,
        )
        if not await reserve_task(runs, task.id, run.id):
            return None
        try:
            await create_run(runs, run)
            await publish_run(jetstream, run.id)
        except BaseException:
            await runs.delete(run.id.hex)
            await release_task(runs, task.id, run.id)
            raise
        task.next_run_at = next_run_after_enqueue(task.schedule_json, now=now)
        return run
    finally:
        await client.drain()


async def enqueue_due_task_runs(session: Session, limit: int = 20) -> int:
    now = datetime.now(UTC)
    statement = select(Task).where(
        Task.archived_at.is_(None), Task.schedule_json.is_not(None),
        Task.next_run_at.is_not(None), Task.next_run_at <= now,
    ).order_by(Task.next_run_at.asc()).limit(limit).with_for_update(skip_locked=True)
    enqueued = 0
    for task in session.scalars(statement):
        if await enqueue_task_run(session, task, "scheduled", now=now) is not None:
            enqueued += 1
    session.flush()
    return enqueued


async def enqueue_ad_hoc_task_run(
    session: Session,
    primitive: TaskPrimitive,
    input_value: dict,
) -> TaskRunSubmission:
    task = get_or_create_ad_hoc_task(session, primitive, input_value)
    run = await enqueue_task_run(session, task, "manual")
    if run is None:
        raise TaskRunConflictError("Task already has an active run.")
    return TaskRunSubmission(task_id=task.id, run_id=run.id)


async def get_task_run(run_id: UUID) -> TaskRunRecord:
    client, _jetstream, runs, _workers = await _storage()
    try:
        run = await get_run(runs, run_id)
    finally:
        await client.drain()
    if run is None:
        raise TaskNotFoundError(f"Task run {run_id} was not found.")
    return _record(run)


async def get_task_run_result(run_id: UUID) -> object:
    client, _jetstream, runs, _workers = await _storage()
    try:
        run = await get_run(runs, run_id)
    finally:
        await client.drain()
    if run is None:
        raise TaskNotFoundError(f"Task run {run_id} was not found.")
    if run.status in _ACTIVE_STATUSES:
        raise TaskRunConflictError("Task run has not finished.")
    if run.status != "succeeded":
        raise RuntimeError(run.error or f"Task run {run.status}.")
    return run.output_json or {}


async def request_task_run_cancellation(run_id: UUID) -> TaskRunRecord:
    client, _jetstream, runs, _workers = await _storage()
    now = datetime.now(UTC)

    def cancel(run: TaskRunState) -> TaskRunState:
        if run.status in {"succeeded", "failed", "cancelled", "skipped"}:
            return run
        updates = {"cancellation_requested_at": now, "updated_at": now}
        if run.status == "queued":
            updates.update(
                status="cancelled",
                cancelled_at=now,
                finished_at=now,
                error="Task run cancelled before execution.",
            )
        return run.model_copy(update=updates)

    try:
        try:
            run = await update_run(runs, run_id, cancel)
        except KeyError as exc:
            raise TaskNotFoundError(f"Task run {run_id} was not found.") from exc
        if run.status == "cancelled":
            await release_task(runs, run.task_id, run.id)
        return _record(run)
    finally:
        await client.drain()


async def task_operations() -> TaskOperationsRecord:
    client, _jetstream, runs_bucket, workers_bucket = await _storage()
    try:
        runs = await list_runs(runs_bucket)
        workers: list[WorkerState] = []
        try:
            for key in await workers_bucket.keys():
                entry = await workers_bucket.get(key)
                workers.append(WorkerState.model_validate_json(entry.value))
        except Exception:
            pass
    finally:
        await client.drain()
    queued = [run for run in runs if run.status == "queued"]
    completed = [run for run in runs if run.started_at is not None and run.finished_at is not None]
    queue_latencies = [(run.started_at - run.queued_at).total_seconds() for run in completed]
    execution_times = [(run.finished_at - run.started_at).total_seconds() for run in completed]
    return TaskOperationsRecord(
        queued=len(queued),
        running=sum(run.status == "running" for run in runs),
        cancelling=sum(run.status == "running" and run.cancellation_requested_at is not None for run in runs),
        stale_workers=0,
        oldest_queued_at=min((run.queued_at for run in queued), default=None),
        average_queue_latency_seconds=sum(queue_latencies) / len(queue_latencies) if queue_latencies else None,
        average_execution_seconds=sum(execution_times) / len(execution_times) if execution_times else None,
        workers=workers,
    )
