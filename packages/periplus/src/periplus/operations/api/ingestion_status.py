"""Read-only ingestion delivery and executor status from existing NATS state."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Request
from nats.js.errors import NotFoundError
from pydantic import BaseModel, ConfigDict

from periplus.ingestion.queue import DURABLE, decode_dead_letter
from periplus.platform.messaging.catalogue_queue import (
    DEAD_LETTER_STREAM, INGEST_DEAD_LETTER_SUBJECT, WORK_STREAM,
)
from periplus.platform.messaging.catalogue_workers import list_catalogue_worker_states
from periplus.operations.api.data_status import (
    DataStatus, QueueStatus, WorkerCapacity, _component_status, _queue_status,
    _worker_capacity,
)

router = APIRouter(prefix="/operations", tags=["operations"])


class Lane(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lane_index: int
    status: Literal["starting", "available", "active", "unavailable"]
    active: bool


class Worker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str
    started_at: datetime
    last_seen_at: datetime
    process_ready: bool
    lanes: tuple[Lane, ...]


class DeadLetter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int
    request_id: str
    kind: Literal["visit", "lineage"]
    failed_at: datetime
    enqueued_at: datetime
    processing_failure_count: int
    error: str


class RecentDeadLetters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[DeadLetter]
    complete: bool
    scanned_sequences: int


class IngestionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: DataStatus
    generated_at: datetime
    queue: QueueStatus
    capacity: WorkerCapacity | None
    workers: tuple[Worker, ...] | None
    dead_letters: int | None
    recent_dead_letters: RecentDeadLetters | None
    issues: list[str]


async def _dead_letters(jetstream):
    info = await jetstream.stream_info(
        DEAD_LETTER_STREAM, subjects_filter=INGEST_DEAD_LETTER_SUBJECT,
    )
    count = int((info.state.subjects or {}).get(INGEST_DEAD_LETTER_SUBJECT, 0))
    items: list[DeadLetter] = []
    scanned = 0
    sequence = info.state.last_seq
    complete = True
    try:
        async with asyncio.timeout(3):
            while count > len(items) and sequence >= info.state.first_seq:
                if scanned >= 200 or len(items) >= 20:
                    complete = False
                    break
                scanned += 1
                try:
                    raw = await jetstream.get_msg(DEAD_LETTER_STREAM, seq=sequence)
                except NotFoundError:
                    sequence -= 1
                    continue
                if raw.subject == INGEST_DEAD_LETTER_SUBJECT:
                    try:
                        entry = decode_dead_letter(raw.data)
                        items.append(DeadLetter(
                            sequence=sequence, request_id=entry.job.request_id,
                            kind=entry.job.kind, failed_at=entry.failed_at,
                            enqueued_at=entry.job.enqueued_at,
                            processing_failure_count=entry.processing_failure_count,
                            error="Ingestion processing failed; inspect worker logs using the request ID.",
                        ))
                    except Exception:
                        complete = False
                sequence -= 1
    except TimeoutError:
        complete = False
    return count, RecentDeadLetters(
        items=items, complete=complete and len(items) == count,
        scanned_sequences=scanned,
    )


@router.get("/ingestion", response_model=IngestionReport)
async def ingestion_status(request: Request) -> IngestionReport:
    queue, states, dead_letters = await asyncio.gather(
        asyncio.wait_for(_queue_status(
            request.app.state.jetstream, stream=WORK_STREAM,
            durable=DURABLE, unit="ingestion_jobs",
        ), timeout=5),
        asyncio.wait_for(list_catalogue_worker_states(
            request.app.state.catalogue_workers,
        ), timeout=5),
        asyncio.wait_for(_dead_letters(request.app.state.jetstream), timeout=5),
        return_exceptions=True,
    )
    issues: list[str] = []
    if isinstance(queue, BaseException):
        queue = QueueStatus(
            available=False, unit="ingestion_jobs", pending=None,
            ack_pending=None, redelivered=None, waiting_for_redelivery=None,
            total=None,
        )
    if not queue.available:
        issues.append("Ingestion consumer state is unavailable.")
    capacity = None
    workers = None
    if isinstance(states, BaseException):
        issues.append("Ingestor presence is unavailable.")
    else:
        capacity = _worker_capacity(states, "ingestion")
        workers = tuple(Worker(
            worker_id=state.worker_id, started_at=state.started_at,
            last_seen_at=state.last_seen_at, process_ready=state.process_ready,
            lanes=tuple(Lane(
                lane_index=lane.lane_index, status=lane.status, active=lane.active,
            ) for lane in state.lanes),
        ) for state in sorted(states, key=lambda state: state.worker_id)
            if state.capability == "ingestion")
    count = None
    recent = None
    if isinstance(dead_letters, BaseException):
        issues.append("Ingestion dead-letter state is unavailable.")
    else:
        count, recent = dead_letters
        if not recent.complete:
            issues.append("Recent dead letters cover only part of the retained entries.")
    status: DataStatus = "unavailable"
    if capacity is not None:
        status = _component_status(queue, capacity, attention=bool(count))
        if count is None and status != "attention":
            status = "unavailable"
    return IngestionReport(
        status=status, generated_at=datetime.now(UTC), queue=queue,
        capacity=capacity, workers=workers, dead_letters=count,
        recent_dead_letters=recent, issues=issues,
    )
