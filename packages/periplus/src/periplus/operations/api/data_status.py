"""User-facing ingestion and materialization operational status."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from nats.js.errors import NotFoundError
from pydantic import BaseModel, ConfigDict

from periplus.ingestion.queue import DURABLE as INGESTION_DURABLE
from periplus.materialization.registry import PROJECTIONS
from periplus.platform.messaging.catalogue_queue import (
    DEAD_LETTER_STREAM,
    INGEST_DEAD_LETTER_SUBJECT,
    WORK_STREAM,
)
from periplus.platform.messaging.catalogue_workers import (
    CatalogueCapability,
    list_catalogue_worker_states,
)


router = APIRouter(prefix="/operations", tags=["operations"])

DataStatus = Literal["current", "processing", "attention", "unavailable"]
QueueUnit = Literal["ingestion_jobs", "materialization_batches"]


class SourceRecordLag(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    available: Literal[False] = False
    unit: Literal["source_records"] = "source_records"
    value: None = None
    reason: str


class QueueStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    available: bool
    unit: QueueUnit
    pending: int | None
    ack_pending: int | None
    redelivered: int | None
    waiting_for_redelivery: int | None
    total: int | None


class WorkerCapacity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_count: int
    configured_capacity: int
    usable_capacity: int
    active_operations: int
    degraded_capacity: int


class IngestionStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DataStatus
    queue: QueueStatus
    workers: WorkerCapacity
    dead_letters: int


class MaterializationWorkloadStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["visits"]
    source: Literal["ingest.visits"]
    projections: tuple[str, ...]
    queue: QueueStatus


class MaterializationStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DataStatus
    source_record_lag: SourceRecordLag
    workloads: tuple[MaterializationWorkloadStatus, ...]
    workers: WorkerCapacity


class MaterializationRunStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    status: Literal[
        "queued",
        "planning",
        "running",
        "activating",
        "completed",
        "failed",
    ]
    total_batches: int
    completed_batches: int
    source_items: int
    source_bytes: int
    output_rows: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None


class DataOperationalStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DataStatus
    generated_at: datetime
    source_record_lag: SourceRecordLag
    ingestion: IngestionStatus
    materialization: MaterializationStatus
    materialization_runs: tuple[MaterializationRunStatus, ...]


_SOURCE_RECORD_LAG = SourceRecordLag(
    reason=(
        "Periplus currently exposes durable rebuild work, not an exact count of "
        "visits inserted since the active generation's source high-water mark."
    )
)

_MATERIALIZATION_WORKLOADS = (
    (
        "visits",
        "ingest.visits",
        tuple(spec.relation.qualified for spec in PROJECTIONS),
        "periplus-materialization-batch-v1",
    ),
)


@router.get("/data-status", response_model=DataOperationalStatus)
async def data_status(
    request: Request,
) -> DataOperationalStatus:
    worker_states_task = asyncio.create_task(
        list_catalogue_worker_states(request.app.state.catalogue_workers)
    )
    ingestion_queue_task = asyncio.create_task(
        _queue_status(
            request.app.state.jetstream,
            stream=WORK_STREAM,
            durable=INGESTION_DURABLE,
            unit="ingestion_jobs",
        )
    )
    materialization_queue_tasks = {
        name: asyncio.create_task(
            _queue_status(
                request.app.state.jetstream,
                stream=WORK_STREAM,
                durable=durable,
                unit="materialization_batches",
            )
        )
        for name, _source, _projections, durable in _MATERIALIZATION_WORKLOADS
    }
    dead_letters_task = asyncio.create_task(
        _ingestion_dead_letter_count(request.app.state.jetstream)
    )
    materialization_runs_task = asyncio.create_task(
        _materialization_runs(request)
    )

    worker_states = await worker_states_task
    ingestion_workers = _worker_capacity(worker_states, "ingestion")
    materialization_workers = _worker_capacity(
        worker_states, "materialization"
    )
    ingestion_queue = await ingestion_queue_task
    dead_letters = await dead_letters_task
    materialization_runs = await materialization_runs_task
    materialization_queues = await asyncio.gather(
        *(
            materialization_queue_tasks[name]
            for name, _source, _projections, _durable
            in _MATERIALIZATION_WORKLOADS
        )
    )
    workloads = tuple(
        MaterializationWorkloadStatus(
            name=name,
            source=source,
            projections=projections,
            queue=queue,
        )
        for (name, source, projections, _durable), queue in zip(
            _MATERIALIZATION_WORKLOADS,
            materialization_queues,
            strict=True,
        )
    )

    ingestion = IngestionStatus(
        status=_component_status(
            ingestion_queue,
            ingestion_workers,
            attention=dead_letters > 0,
        ),
        queue=ingestion_queue,
        workers=ingestion_workers,
        dead_letters=dead_letters,
    )
    materialization = MaterializationStatus(
        status=_materialization_status(
            workloads,
            materialization_workers,
        ),
        source_record_lag=_SOURCE_RECORD_LAG,
        workloads=workloads,
        workers=materialization_workers,
    )
    return DataOperationalStatus(
        status=_overall_status(ingestion.status, materialization.status),
        generated_at=datetime.now(UTC),
        source_record_lag=_SOURCE_RECORD_LAG,
        ingestion=ingestion,
        materialization=materialization,
        materialization_runs=materialization_runs,
    )


async def _queue_status(
    jetstream,
    *,
    stream: str,
    durable: str,
    unit: QueueUnit,
) -> QueueStatus:
    try:
        info = await jetstream.consumer_info(stream, durable)
    except NotFoundError:
        return QueueStatus(
            available=False,
            unit=unit,
            pending=None,
            ack_pending=None,
            redelivered=None,
            waiting_for_redelivery=None,
            total=None,
        )
    pending = int(info.num_pending or 0)
    ack_pending = int(info.num_ack_pending or 0)
    redelivered = int(info.num_redelivered or 0)
    return QueueStatus(
        available=True,
        unit=unit,
        pending=pending,
        ack_pending=ack_pending,
        redelivered=redelivered,
        # JetStream does not expose individual delayed-NAK deadlines, and
        # shared worker capacity cannot prove which ACK-pending delivery is
        # active. Do not present a heuristic as a server count.
        waiting_for_redelivery=None,
        total=pending + ack_pending,
    )


async def _ingestion_dead_letter_count(jetstream) -> int:
    try:
        info = await jetstream.stream_info(
            DEAD_LETTER_STREAM,
            subjects_filter=INGEST_DEAD_LETTER_SUBJECT,
        )
    except NotFoundError:
        return 0
    return int(info.state.messages or 0)


def _worker_capacity(
    states,
    capability: CatalogueCapability,
) -> WorkerCapacity:
    workers = [state for state in states if state.capability == capability]
    configured = sum(worker.configured_capacity for worker in workers)
    usable = sum(
        worker.usable_capacity for worker in workers if worker.process_ready
    )
    return WorkerCapacity(
        worker_count=len(workers),
        configured_capacity=configured,
        usable_capacity=usable,
        active_operations=sum(
            worker.active_operation_count for worker in workers
        ),
        degraded_capacity=max(0, configured - usable),
    )


def _component_status(
    queue: QueueStatus,
    workers: WorkerCapacity,
    *,
    attention: bool = False,
) -> DataStatus:
    if attention or workers.degraded_capacity > 0:
        return "attention"
    if not queue.available or workers.usable_capacity == 0:
        return "unavailable"
    assert queue.total is not None
    if queue.total == 0:
        return "current"
    if workers.active_operations > 0:
        return "processing"
    return "attention"


def _materialization_status(
    workloads: tuple[MaterializationWorkloadStatus, ...],
    workers: WorkerCapacity,
) -> DataStatus:
    statuses = tuple(
        _component_status(workload.queue, workers) for workload in workloads
    )
    if "attention" in statuses:
        return "attention"
    if "unavailable" in statuses:
        return "unavailable"
    if "processing" in statuses:
        return "processing"
    return "current"


def _overall_status(
    ingestion: DataStatus,
    materialization: DataStatus,
) -> DataStatus:
    for status in ("attention", "unavailable", "processing", "current"):
        if ingestion == status or materialization == status:
            return status
    raise AssertionError("unreachable data status")


async def _materialization_runs(
    request: Request,
) -> tuple[MaterializationRunStatus, ...]:
    store = getattr(request.app.state, "materialization_runs", None)
    if store is None:
        return ()
    runs = await store.list(limit=10)
    return tuple(_materialization_run_status(run) for run in runs)


def _materialization_run_status(run) -> MaterializationRunStatus:
    return MaterializationRunStatus(
        id=run.id,
        status=run.status,
        total_batches=run.total_batches,
        completed_batches=run.completed_batches,
        source_items=run.source_items,
        source_bytes=run.source_bytes,
        output_rows=run.output_rows,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error=run.error,
    )
