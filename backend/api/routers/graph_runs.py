"""Current crawl-graph run API."""

import asyncio
import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from nats.js.errors import NotFoundError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.graph_submission import submit_graph_run
from api.graph_runtime import ApiGraphRuntime, get_graph_runtime
from api.catalogue_control import CatalogueControl, get_catalogue_control
from config.performance import (
    CRAWL_ACQUISITION_LANES,
    GRAPH_CONSUMER_MAX_ACK_PENDING,
    INGESTION_QUACK_CLIENTS,
    MATERIALIZATION_QUACK_CLIENTS,
    duckdb_memory_limit,
    duckdb_threads,
)
from control.crawl_graphs.models import CrawlGraph
from control.crawl_graphs.schemas import (
    DEFAULT_GRAPH_RUN_MAX_CRAWLS,
    MAX_GRAPH_RUN_CRAWLS,
)
from control.crawl_graphs.service import (
    CrawlGraphNotFoundError,
    CrawlGraphValidationError,
)
from db.session import get_session
from repository.ingestion.queue import DURABLE as INGESTION_DURABLE
from cdc.events import DML_SUBJECT_PREFIX, EVENT_STREAM
from runtime.catalogue_queue import WORK_STREAM
from runtime.graph_queue import (
    GraphRun,
    get_graph_run,
    list_graph_runs,
    list_worker_states,
)
from runtime.graph_runs import (
    GraphRunNotFoundError,
    pause_graph_run,
    request_cancellation,
    resume_graph_run,
)
from runtime.catalogue_workers import (
    CatalogueCapability,
    list_catalogue_worker_states,
)

router = APIRouter(prefix="/graph-runs", tags=["graph-runs"])
trigger_router = APIRouter(prefix="/crawl-graphs", tags=["crawl-graphs"])


class GraphRunTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: list[str] = Field(min_length=1, max_length=10_000)
    max_crawls: int = Field(
        default=DEFAULT_GRAPH_RUN_MAX_CRAWLS,
        ge=1,
        le=MAX_GRAPH_RUN_CRAWLS,
    )
    max_run_seconds: int | None = Field(
        default=None,
        ge=60,
        le=365 * 24 * 60 * 60,
    )


class GraphRunSubmission(BaseModel):
    graph_id: UUID
    run_id: UUID
    status: str = "queued"


class GraphRunSummary(BaseModel):
    id: UUID
    graph_id: UUID
    graph_slug: str | None
    status: Literal[
        "queued",
        "running",
        "paused",
        "completed",
        "completed_with_errors",
        "failed",
        "cancelled",
    ]
    trigger_kind: Literal["manual", "schedule"]
    trigger_schedule_id: UUID | None
    trigger_urls: tuple[str, ...]
    max_crawls: int
    crawl_limit_reached: bool
    request_count: int
    pending_request_count: int
    failed_request_count: int
    error_count: int
    queued_request_count: int
    fetching_request_count: int
    navigating_request_count: int
    created_at: datetime
    started_at: datetime | None
    last_progress_at: datetime | None
    completed_at: datetime | None
    paused_at: datetime | None
    not_before: datetime | None
    deadline_at: datetime | None
    cancel_requested_at: datetime | None
    error: str | None


class GraphRunList(BaseModel):
    items: list[GraphRunSummary]
    total: int


class RuntimeWorkerCapacity(BaseModel):
    worker_id: str
    capacity: int
    active_request_count: int
    last_seen_at: datetime


class CatalogueExecutorCapacity(BaseModel):
    capability: CatalogueCapability
    worker_count: int
    configured_capacity: int
    capacity: int
    active: int
    degraded: int
    backlog: int
    pending: int
    ack_pending: int
    redelivered: int
    waiting_for_redelivery: int


class CrawlConcurrencyLimits(BaseModel):
    worker_count: int
    runtime_capacity: int
    runtime_active: int
    workers: list[RuntimeWorkerCapacity]
    catalogue_executors: list[CatalogueExecutorCapacity]
    tuning: "RuntimeSizing"


class RuntimeSizing(BaseModel):
    crawl_lanes_per_replica: int
    ingestion_clients_per_replica: int
    materialization_clients_per_replica: int
    graph_consumer_delivery_ceiling: int
    duckdb_threads_per_client: int
    duckdb_memory_limit_per_client: str


class GraphRunFailureGroupResponse(BaseModel):
    failure_stage: str
    failure_code: str
    status_code: int | None
    count: int
    example_url: str
    example_detail: str | None
    last_occurred_at: datetime


class GraphRunFailureSummary(BaseModel):
    items: list[GraphRunFailureGroupResponse]
    total: int


async def _run_summaries(
    session: Session,
    progress,
    runs: list[GraphRun],
) -> list[GraphRunSummary]:
    stage_counts = await asyncio.gather(
        *(_run_stage_counts(progress, run) for run in runs)
    )
    graph_ids = {run.graph_id for run in runs}
    slugs = (
        dict(
            session.execute(
                select(CrawlGraph.id, CrawlGraph.slug).where(
                    CrawlGraph.id.in_(graph_ids)
                )
            ).all()
        )
        if graph_ids
        else {}
    )
    return [
        GraphRunSummary(
            **run.model_dump(),
            graph_slug=slugs.get(run.graph_id),
            queued_request_count=counts[0],
            fetching_request_count=counts[1],
            navigating_request_count=counts[2],
        )
        for run, counts in zip(runs, stage_counts, strict=True)
    ]


async def _run_stage_counts(progress, run: GraphRun) -> tuple[int, int, int]:
    counts = await progress.progress_counts(run.id)
    queued = sum(
        count
        for (_node_id, status), count in counts.items()
        if status == "queued"
    )
    fetching = sum(
        count
        for (_node_id, status), count in counts.items()
        if status == "crawling"
    )
    navigating = sum(
        count
        for (_node_id, status), count in counts.items()
        if status in {"awaiting_navigation", "evaluating_edges"}
    )
    return queued, fetching, navigating


@router.get("/capacity", response_model=CrawlConcurrencyLimits)
async def capacity(
    runtime: Annotated[ApiGraphRuntime, Depends(get_graph_runtime)],
) -> CrawlConcurrencyLimits:
    workers = sorted(
        await list_worker_states(runtime.workers),
        key=lambda value: value.worker_id,
    )
    catalogue_workers = sorted(
        await list_catalogue_worker_states(runtime.catalogue_workers),
        key=lambda value: value.worker_id,
    )

    async def consumer_counts(durable: str) -> tuple[int, int, int]:
        try:
            info = await runtime.jetstream.consumer_info(WORK_STREAM, durable)
        except NotFoundError:
            return 0, 0, 0
        return (
            int(info.num_pending or 0),
            int(info.num_ack_pending or 0),
            int(info.num_redelivered or 0),
        )

    try:
        materialization_consumers = await runtime.jetstream.consumers_info(
            EVENT_STREAM
        )
    except NotFoundError:
        materialization_consumers = []
    active_materialization_consumers = {
        "atlas-material-html_elements-v1",
        "atlas-material-jsonld_values-v1",
        "atlas-material-pages-v1",
        "atlas-material-links-v1",
    }
    relevant_materialization_consumers = [
        info
        for info in materialization_consumers
        if (
            (info.config.filter_subject or "").startswith(
                f"{DML_SUBJECT_PREFIX}."
            )
            and (info.name or info.config.durable_name)
            in active_materialization_consumers
        )
    ]
    catalogue_queues = {
        "ingestion": await consumer_counts(INGESTION_DURABLE),
        "materialization": (
            sum(
                int(info.num_pending or 0)
                for info in relevant_materialization_consumers
            ),
            sum(
                int(info.num_ack_pending or 0)
                for info in relevant_materialization_consumers
            ),
            sum(
                int(info.num_redelivered or 0)
                for info in relevant_materialization_consumers
            ),
        ),
    }
    catalogue_executors: list[CatalogueExecutorCapacity] = []
    for capability in ("ingestion", "materialization"):
        configured_capacity = sum(
            worker.configured_capacity
            for worker in catalogue_workers
            if worker.capability == capability
        )
        usable_capacity = sum(
            worker.usable_capacity
            for worker in catalogue_workers
            if worker.capability == capability and worker.process_ready
        )
        active = sum(
            worker.active_operation_count
            for worker in catalogue_workers
            if worker.capability == capability
        )
        pending, ack_pending, redelivered = catalogue_queues[capability]
        catalogue_executors.append(
            CatalogueExecutorCapacity(
                capability=capability,
                worker_count=sum(
                    worker.capability == capability
                    for worker in catalogue_workers
                ),
                configured_capacity=configured_capacity,
                capacity=usable_capacity,
                active=active,
                degraded=configured_capacity - usable_capacity,
                backlog=pending + ack_pending,
                pending=pending,
                ack_pending=ack_pending,
                redelivered=redelivered,
                # JetStream does not publish per-message delayed-NAK deadlines.
                # A quiescent consumer with only ACK-pending deliveries is the
                # bounded, server-authoritative wait state we can prove.
                waiting_for_redelivery=(
                    ack_pending if pending == 0 and active == 0 else 0
                ),
            )
        )
    return CrawlConcurrencyLimits(
        worker_count=len(workers),
        runtime_capacity=sum(worker.capacity for worker in workers),
        runtime_active=sum(worker.active_request_count for worker in workers),
        workers=[
            RuntimeWorkerCapacity.model_validate(worker, from_attributes=True)
            for worker in workers
        ],
        catalogue_executors=catalogue_executors,
        tuning=RuntimeSizing(
            crawl_lanes_per_replica=CRAWL_ACQUISITION_LANES,
            ingestion_clients_per_replica=INGESTION_QUACK_CLIENTS,
            materialization_clients_per_replica=MATERIALIZATION_QUACK_CLIENTS,
            graph_consumer_delivery_ceiling=GRAPH_CONSUMER_MAX_ACK_PENDING,
            duckdb_threads_per_client=duckdb_threads(),
            duckdb_memory_limit_per_client=duckdb_memory_limit(),
        ),
    )


@trigger_router.post(
    "/{graph_id}/runs", response_model=GraphRunSubmission, status_code=202
)
async def trigger(
    graph_id: UUID,
    payload: GraphRunTrigger,
    session: Annotated[Session, Depends(get_session)],
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
    runtime: Annotated[ApiGraphRuntime, Depends(get_graph_runtime)],
) -> GraphRunSubmission:
    try:
        run = await submit_graph_run(
            session,
            runtime=runtime,
            graph_id=graph_id,
            urls=payload.urls,
            catalogue_snapshot_resolver=control.latest_snapshot,
            max_crawls=payload.max_crawls,
            max_run_seconds=payload.max_run_seconds,
        )
    except CrawlGraphNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CrawlGraphValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return GraphRunSubmission(graph_id=graph_id, run_id=run.id)


@trigger_router.get("/{graph_id}/runs/active", response_model=GraphRunList)
async def active_graph_runs(
    graph_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    runtime: Annotated[ApiGraphRuntime, Depends(get_graph_runtime)],
) -> GraphRunList:
    items = [
        run
        for run in await list_graph_runs(runtime.runs)
        if run.graph_id == graph_id
        and run.status in {"queued", "running", "paused"}
    ]
    items.sort(key=lambda run: run.created_at, reverse=True)
    summaries = await _run_summaries(session, runtime.runs, items)
    return GraphRunList(
        items=summaries,
        total=len(items),
    )


@router.get("/{run_id}", response_model=GraphRun)
async def get(
    run_id: UUID,
    runtime: Annotated[ApiGraphRuntime, Depends(get_graph_runtime)],
) -> GraphRun:
    run = await get_graph_run(runtime.runs, run_id)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"Graph run {run_id} was not found."
        )
    return run


@router.get("/{run_id}/failure-summary", response_model=GraphRunFailureSummary)
async def failure_summary(
    run_id: UUID,
    runtime: Annotated[ApiGraphRuntime, Depends(get_graph_runtime)],
) -> GraphRunFailureSummary:
    run = await get_graph_run(runtime.runs, run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"Graph run {run_id} was not found.",
        )
    groups = sorted(
        run.failure_groups,
        key=lambda group: (-group.count, group.failure_stage, group.failure_code),
    )
    return GraphRunFailureSummary(
        items=[
            GraphRunFailureGroupResponse.model_validate(group, from_attributes=True)
            for group in groups
        ],
        total=run.failed_request_count,
    )


@router.get("/", response_model=GraphRunList)
async def list_runs(
    session: Annotated[Session, Depends(get_session)],
    runtime: Annotated[ApiGraphRuntime, Depends(get_graph_runtime)],
) -> GraphRunList:
    items = await list_graph_runs(runtime.runs)
    items.sort(key=lambda run: run.created_at, reverse=True)
    summaries = await _run_summaries(session, runtime.runs, items)
    return GraphRunList(
        items=summaries,
        total=len(items),
    )


async def _run_events(request: Request, runs, progress, run: GraphRun):
    revision = 0
    previous = None
    last_heartbeat = asyncio.get_running_loop().time()
    while not await request.is_disconnected():
        nodes, edges = await progress.progress_snapshot(run.id)
        payload = json.dumps(
            {
                "graph_run_id": str(run.id),
                "nodes": {
                    str(node.node_id): node.model_dump(mode="json")
                    for node in nodes
                },
                "edges": {
                    str(edge.edge_id): edge.model_dump(mode="json")
                    for edge in edges
                },
            },
            separators=(",", ":"),
        )
        if payload != previous:
            revision += 1
            previous = payload
            yield (
                f"id: {revision}\nevent: progress_snapshot"
                f"\ndata: {payload}\n\n"
            )
        current = await get_graph_run(runs, run.id) or run
        if current.status not in {"queued", "running", "paused"}:
            yield (
                "event: run_settled\ndata: "
                f"{current.model_dump_json()}\n\n"
            )
            return
        now = asyncio.get_running_loop().time()
        if now - last_heartbeat >= 10:
            yield "event: heartbeat\ndata: {}\n\n"
            last_heartbeat = now
        await asyncio.sleep(1)


@router.get("/{run_id}/events")
async def run_events(
    run_id: UUID,
    request: Request,
    runtime: Annotated[ApiGraphRuntime, Depends(get_graph_runtime)],
) -> StreamingResponse:
    run = await get_graph_run(runtime.runs, run_id)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"Graph run {run_id} was not found."
        )
    return StreamingResponse(
        _run_events(request, runtime.runs, runtime.runs, run),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{run_id}/cancel", response_model=GraphRun)
async def cancel(
    run_id: UUID,
    runtime: Annotated[ApiGraphRuntime, Depends(get_graph_runtime)],
) -> GraphRun:
    try:
        return await request_cancellation(
            runtime.runs,
            runtime.requests,
            run_id,
            progress=runtime.runs,
        )
    except GraphRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{run_id}/pause", response_model=GraphRun)
async def pause(
    run_id: UUID,
    runtime: Annotated[ApiGraphRuntime, Depends(get_graph_runtime)],
) -> GraphRun:
    try:
        return await pause_graph_run(runtime.runs, run_id)
    except GraphRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{run_id}/resume", response_model=GraphRun)
async def resume(
    run_id: UUID,
    runtime: Annotated[ApiGraphRuntime, Depends(get_graph_runtime)],
) -> GraphRun:
    try:
        return await resume_graph_run(runtime.runs, run_id)
    except GraphRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
