"""Crawl-specific fixed materialization barrier."""

from __future__ import annotations

import asyncio
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from nats.js.errors import NotFoundError as NatsNotFoundError
from pydantic import BaseModel, Field

from atlas.crawl.api_runtime import ApiGraphRuntime, get_graph_runtime
from atlas.crawl.runtime.graph_queue import (
    GraphRun,
    get_graph_run,
    list_crawl_requests,
)
from atlas.ingestion.queue import (
    crawl_ingestion_request_id,
    get_ingestion_state,
    visit_ingestion_request_id,
)
from atlas.materialization.cdc.events import DMLTick, EVENT_STREAM
from atlas.materialization.executor import workloads

router = APIRouter(prefix="/graph-runs", tags=["graph-runs"])

BarrierStatus = Literal[
    "waiting_acquisition",
    "waiting_ingestion",
    "waiting_materialization",
    "materialized",
    "failed",
]
_TERMINAL_RUN_STATUSES = {
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
}
_WORKLOADS = {workload.name: workload for workload in workloads()}
_DOCUMENT_WORKLOAD = _WORKLOADS["documents"]
_VISIT_WORKLOAD = _WORKLOADS["visits"]
_INGESTION_LOOKUP_BATCH = 128


class MaterializationSnapshots(BaseModel):
    documents: int | None = None
    visits: int | None = None


class CrawlMaterializationBarrier(BaseModel):
    status: BarrierStatus
    required_snapshots: MaterializationSnapshots = Field(
        default_factory=MaterializationSnapshots
    )
    committed_snapshots: MaterializationSnapshots = Field(
        default_factory=MaterializationSnapshots
    )
    error: str | None = None


def get_ingestion_results(request: Request) -> Any:
    service = getattr(request.app.state, "evidence_import_service", None)
    results = getattr(getattr(service, "queue", None), "results", None)
    if results is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ingestion result store is unavailable",
        )
    return results


async def _committed_snapshot(
    runtime: ApiGraphRuntime,
    *,
    durable: str,
    source_table: str,
) -> int | None:
    try:
        info = await runtime.jetstream.consumer_info(EVENT_STREAM, durable)
    except NatsNotFoundError:
        return None

    stream_sequence = int(info.ack_floor.stream_seq)
    if stream_sequence <= 0:
        return None

    try:
        message = await runtime.jetstream.get_msg(
            EVENT_STREAM,
            seq=stream_sequence,
        )
    except NatsNotFoundError:
        return None

    try:
        tick = DMLTick.model_validate_json(message.data)
    except ValueError as exc:
        raise RuntimeError(
            f"Materialization consumer {durable!r} acknowledged an invalid CDC tick"
        ) from exc
    if tick.table_name != source_table:
        raise RuntimeError(
            f"Materialization consumer {durable!r} acknowledged an unexpected CDC table"
        )
    return tick.end_snapshot


def _failed_barrier() -> CrawlMaterializationBarrier:
    return CrawlMaterializationBarrier(
        status="failed",
        error="One or more crawl ingestion jobs failed",
    )


async def resolve_materialization_barrier(
    run: GraphRun,
    *,
    runtime: ApiGraphRuntime,
    results: Any,
) -> CrawlMaterializationBarrier:
    if run.status not in _TERMINAL_RUN_STATUSES:
        return CrawlMaterializationBarrier(status="waiting_acquisition")

    crawl_requests = await list_crawl_requests(
        runtime.requests,
        graph_run_id=run.id,
    )
    if len(crawl_requests) != run.request_count:
        return CrawlMaterializationBarrier(status="waiting_ingestion")

    crawl_state = await get_ingestion_state(
        results,
        crawl_ingestion_request_id(run.id),
    )
    if crawl_state is not None and crawl_state.status == "failed":
        return _failed_barrier()
    if crawl_state is None or crawl_state.status != "succeeded":
        return CrawlMaterializationBarrier(status="waiting_ingestion")

    required_visits: int | None = None
    required_documents: int | None = None
    visit_ids = [
        visit_ingestion_request_id(item.id) for item in crawl_requests
    ]
    for offset in range(0, len(visit_ids), _INGESTION_LOOKUP_BATCH):
        batch = visit_ids[offset : offset + _INGESTION_LOOKUP_BATCH]
        states = await asyncio.gather(
            *(get_ingestion_state(results, item) for item in batch)
        )
        if any(
            state is not None and state.status == "failed"
            for state in states
        ):
            return _failed_barrier()
        if any(
            state is None or state.status != "succeeded"
            for state in states
        ):
            return CrawlMaterializationBarrier(
                status="waiting_ingestion"
            )
        for state in states:
            assert state is not None
            assert state.result is not None
            required_visits = max(
                required_visits or 0,
                state.result.repository_snapshot,
            )
            if (
                state.job.visit is not None
                and state.job.visit.document is not None
            ):
                required_documents = max(
                    required_documents or 0,
                    state.result.repository_snapshot,
                )
    required = MaterializationSnapshots(
        documents=required_documents,
        visits=required_visits,
    )

    committed_documents, committed_visits = await asyncio.gather(
        _committed_snapshot(
            runtime,
            durable=_DOCUMENT_WORKLOAD.durable,
            source_table=_DOCUMENT_WORKLOAD.source_table,
        ),
        _committed_snapshot(
            runtime,
            durable=_VISIT_WORKLOAD.durable,
            source_table=_VISIT_WORKLOAD.source_table,
        ),
    )
    committed = MaterializationSnapshots(
        documents=committed_documents,
        visits=committed_visits,
    )
    documents_ready = required.documents is None or (
        committed.documents is not None
        and committed.documents >= required.documents
    )
    visits_ready = required.visits is None or (
        committed.visits is not None and committed.visits >= required.visits
    )
    return CrawlMaterializationBarrier(
        status=(
            "materialized"
            if documents_ready and visits_ready
            else "waiting_materialization"
        ),
        required_snapshots=required,
        committed_snapshots=committed,
    )


@router.get(
    "/{run_id}/materialization",
    response_model=CrawlMaterializationBarrier,
)
async def get_materialization_barrier(
    run_id: UUID,
    runtime: ApiGraphRuntime = Depends(get_graph_runtime),
    results: Any = Depends(get_ingestion_results),
) -> CrawlMaterializationBarrier:
    run = await get_graph_run(runtime.runs, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Graph run {run_id} not found",
        )
    return await resolve_materialization_barrier(
        run,
        runtime=runtime,
        results=results,
    )
