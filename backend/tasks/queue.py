"""JetStream task work and KV-backed current run state."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import nats
from nats.js.api import AckPolicy, ConsumerConfig, KeyValueConfig, RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import BadRequestError, BucketNotFoundError, KeyDeletedError, KeyNotFoundError, KeyWrongLastSequenceError, NotFoundError
from pydantic import BaseModel, ConfigDict, Field

from tasks.schemas import TaskPrimitive, TaskRunStatus, TaskRunTriggerKind

TASK_STREAM = "ATLAS_TASKS"
TASK_SUBJECT = "atlas.tasks.run"
TASK_CONSUMER = "atlas-task-workers"
RUNS_BUCKET = "ATLAS_TASK_RUNS"
WORKERS_BUCKET = "ATLAS_TASK_WORKERS"


class TaskRunState(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    task_id: UUID
    task_revision: int
    primitive: TaskPrimitive
    status: TaskRunStatus = "queued"
    trigger_kind: TaskRunTriggerKind
    data_schema_id: UUID | None = None
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancellation_requested_at: datetime | None = None
    cancelled_at: datetime | None = None
    attempt: int = 0
    failed_attempts: int = 0
    max_attempts: int = 3
    input_json: dict
    crawl_policy_snapshots_json: list[dict] = Field(default_factory=list)
    output_json: object | None = None
    warnings_json: dict = Field(default_factory=dict)
    error: str | None = None
    worker_id: str | None = None
    execution_token: UUID | None = None
    created_at: datetime
    updated_at: datetime


class TaskWork(BaseModel):
    model_config = ConfigDict(frozen=True)
    run_id: UUID


class WorkerState(BaseModel):
    model_config = ConfigDict(frozen=True)
    worker_id: str
    started_at: datetime
    last_seen_at: datetime
    capacity: int
    active_run_count: int
    stopping: bool = False
    version: str | None = None


async def connect_nats():
    return await nats.connect(os.getenv("NATS_URL", "nats://127.0.0.1:4222"), connect_timeout=2, max_reconnect_attempts=-1)


async def ensure_task_storage(jetstream):
    stream = StreamConfig(name=TASK_STREAM, subjects=[TASK_SUBJECT], retention=RetentionPolicy.WORK_QUEUE, storage=StorageType.FILE, num_replicas=_positive_int("ATLAS_TASK_STREAM_REPLICAS", 1))
    try:
        await jetstream.stream_info(TASK_STREAM)
    except NotFoundError:
        await jetstream.add_stream(config=stream)
    consumer = ConsumerConfig(durable_name=TASK_CONSUMER, ack_policy=AckPolicy.EXPLICIT, ack_wait=float(os.getenv("ATLAS_TASK_ACK_WAIT_SECONDS", "60")), filter_subject=TASK_SUBJECT, max_ack_pending=_positive_int("ATLAS_WORKER_CONCURRENCY", 4), max_deliver=-1)
    await jetstream.add_consumer(TASK_STREAM, config=consumer)
    runs = await _bucket(jetstream, KeyValueConfig(bucket=RUNS_BUCKET, description="Current Atlas task-run state", history=1, max_bytes=_positive_int("ATLAS_TASK_RUN_STATE_MAX_BYTES", 256 * 1024 * 1024), storage=StorageType.FILE, replicas=_positive_int("ATLAS_TASK_STREAM_REPLICAS", 1)))
    workers = await _bucket(jetstream, KeyValueConfig(bucket=WORKERS_BUCKET, description="Ephemeral Atlas task-worker presence", history=1, ttl=float(os.getenv("ATLAS_WORKER_PRESENCE_TTL_SECONDS", "30")), storage=StorageType.FILE, replicas=_positive_int("ATLAS_TASK_STREAM_REPLICAS", 1)))
    return runs, workers


async def _bucket(jetstream, config: KeyValueConfig):
    try:
        return await jetstream.key_value(config.bucket)
    except BucketNotFoundError:
        try:
            return await jetstream.create_key_value(config=config)
        except BadRequestError:
            return await jetstream.key_value(config.bucket)


def run_key(run_id: UUID) -> str:
    return run_id.hex


def active_key(task_id: UUID) -> str:
    return f"active-{task_id.hex}"


async def reserve_task(bucket, task_id: UUID, run_id: UUID) -> bool:
    key = active_key(task_id)
    while True:
        try:
            await bucket.create(key, run_id.hex.encode())
            return True
        except KeyWrongLastSequenceError:
            try:
                entry = await bucket.get(key)
                existing = await get_run(bucket, UUID(hex=entry.value.decode()))
            except (KeyNotFoundError, KeyDeletedError, ValueError):
                continue
            if existing is not None and existing.status in {"queued", "running"}:
                return False
            try:
                await bucket.delete(key, last=entry.revision)
            except KeyWrongLastSequenceError:
                continue


async def release_task(bucket, task_id: UUID, run_id: UUID) -> None:
    try:
        entry = await bucket.get(active_key(task_id))
    except (KeyNotFoundError, KeyDeletedError):
        return
    if entry.value.decode() == run_id.hex:
        await bucket.delete(active_key(task_id))


async def get_run(bucket, run_id: UUID) -> TaskRunState | None:
    try:
        entry = await bucket.get(run_key(run_id))
    except (KeyNotFoundError, KeyDeletedError):
        return None
    return TaskRunState.model_validate_json(entry.value)


async def create_run(bucket, state: TaskRunState) -> None:
    await bucket.create(run_key(state.id), state.model_dump_json().encode())


async def update_run(bucket, run_id: UUID, mutate) -> TaskRunState:
    while True:
        try:
            entry = await bucket.get(run_key(run_id))
        except (KeyNotFoundError, KeyDeletedError) as exc:
            raise KeyError(str(run_id)) from exc
        current = TaskRunState.model_validate_json(entry.value)
        updated = mutate(current)
        if updated is current:
            return current
        try:
            await bucket.update(run_key(run_id), updated.model_dump_json().encode(), last=entry.revision)
            return updated
        except KeyWrongLastSequenceError:
            continue


async def list_runs(bucket) -> list[TaskRunState]:
    try:
        keys = await bucket.keys()
    except (KeyNotFoundError, KeyDeletedError):
        return []
    values = []
    for key in keys:
        if key.startswith("active-"):
            continue
        try:
            entry = await bucket.get(key)
        except (KeyNotFoundError, KeyDeletedError):
            continue
        values.append(TaskRunState.model_validate_json(entry.value))
    return values


async def publish_run(jetstream, run_id: UUID) -> None:
    await jetstream.publish(TASK_SUBJECT, TaskWork(run_id=run_id).model_dump_json().encode(), stream=TASK_STREAM, headers={"Nats-Msg-Id": run_id.hex})


def new_run(*, task_id: UUID, task_revision: int, primitive: TaskPrimitive, trigger_kind: TaskRunTriggerKind, input_json: dict, crawl_policy_snapshots_json: list[dict], data_schema_id: UUID | None = None, now: datetime | None = None) -> TaskRunState:
    now = now or datetime.now(UTC)
    return TaskRunState(id=uuid4(), task_id=task_id, task_revision=task_revision, primitive=primitive, trigger_kind=trigger_kind, data_schema_id=data_schema_id, queued_at=now, input_json=input_json, crawl_policy_snapshots_json=crawl_policy_snapshots_json, created_at=now, updated_at=now)


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value
