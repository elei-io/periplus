"""Current crawl-graph run API."""

import asyncio
import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.errors import NotFoundError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.graph_submission import submit_graph_run
from api.catalogue_control import CatalogueControl, get_catalogue_control
from config.performance import (
    GRAPH_CONSUMER_MAX_ACK_PENDING,
    CRAWL_ACQUISITION_LANES,
    RESOURCE_ACQUIRE_TIMEOUT_SECONDS,
    catalogue_max_concurrency,
    duckdb_memory_limit,
    duckdb_threads,
    object_io_max_concurrency,
)
from control.crawl_graphs.models import CrawlGraph
from control.crawl_graphs.service import (
    CrawlGraphNotFoundError,
    CrawlGraphValidationError,
)
from db.session import get_session
from repository.ingestion.queue import (
    DURABLE as INGESTION_DURABLE,
    IngestionState,
    crawl_ingestion_request_id,
    ensure_ingestion_results,
    get_ingestion_state,
)
from materialization.queue import (
    SCOPE_BACKFILL_DURABLE,
    SCOPE_LIVE_DURABLE,
)
from runtime.catalogue_queue import WORK_STREAM
from runtime.graph_queue import (
    CrawlRequest,
    GraphRun,
    connect_nats,
    ensure_graph_progress_storage,
    ensure_graph_storage,
    get_graph_run,
    list_crawl_requests,
    list_graph_runs,
    list_worker_states,
)
from runtime.graph_runs import (
    GraphRunNotFoundError,
    request_cancellation,
)
from runtime.catalogue_workers import (
    CatalogueCapability,
    ensure_catalogue_worker_storage,
    list_catalogue_worker_states,
)
from runtime.graph_progress import (
    EdgeProgress,
    NodeProgress,
    bootstrap_run_progress,
    node_progress_key,
)
from runtime.resource_governor import (
    ResourceUsage,
    ensure_resource_governor_storage,
    resource_usage,
)

router = APIRouter(prefix="/graph-runs", tags=["graph-runs"])
trigger_router = APIRouter(prefix="/crawl-graphs", tags=["crawl-graphs"])


class GraphRunTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: list[str] = Field(min_length=1, max_length=10_000)


class GraphRunSubmission(BaseModel):
    graph_id: UUID
    run_id: UUID
    status: str = "queued"


class GraphRunSummary(BaseModel):
    id: UUID
    graph_id: UUID
    graph_slug: str | None
    status: Literal[
        "queued", "running", "completed", "completed_with_errors", "failed", "cancelled"
    ]
    trigger_kind: Literal["manual", "schedule"]
    trigger_schedule_id: UUID | None
    trigger_urls: tuple[str, ...]
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
    capacity: int
    active: int
    backlog: int


class CrawlConcurrencyLimits(BaseModel):
    worker_count: int
    runtime_capacity: int
    runtime_active: int
    resource_acquire_timeout_seconds: float
    resources: list[ResourceUsage]
    workers: list[RuntimeWorkerCapacity]
    catalogue_executors: list[CatalogueExecutorCapacity]
    tuning: RuntimeSizing


class RuntimeSizing(BaseModel):
    catalogue_max_concurrency: int
    effective_catalogue_concurrency: int
    object_io_max_concurrency: int
    crawl_lanes_per_replica: int
    catalogue_lanes_per_replica: int
    graph_consumer_delivery_ceiling: int
    duckdb_threads_per_executor: int
    duckdb_memory_limit_per_executor: str


class GraphRunFailure(BaseModel):
    crawl_id: UUID
    requested_url: str
    final_url: str | None
    status_code: int | None
    failure_code: str | None
    failure_stage: str | None
    failure_detail: str | None
    captured_at: datetime


class GraphRunFailureList(BaseModel):
    items: list[GraphRunFailure]
    total: int


async def _run_summaries(
    session: Session,
    progress,
    runs: list[GraphRun],
) -> list[GraphRunSummary]:
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
    stage_counts = await asyncio.gather(
        *(_run_stage_counts(progress, run) for run in runs)
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
    entries = await asyncio.gather(
        *(
            progress.get(node_progress_key(run.id, node.id))
            for node in run.snapshot.nodes
        ),
        return_exceptions=True,
    )
    nodes: list[NodeProgress] = []
    for entry in entries:
        if isinstance(entry, Exception):
            continue
        nodes.append(NodeProgress.model_validate_json(entry.value))
    pending = run.pending_request_count
    queued = min(pending, sum(node.queued for node in nodes))
    fetching = min(pending - queued, sum(node.crawling for node in nodes))
    navigating = max(0, pending - queued - fetching)
    return queued, fetching, navigating


async def _storage():
    client = await connect_nats()
    jetstream = client.jetstream()
    runs, requests, _workers = await ensure_graph_storage(jetstream)
    progress = await ensure_graph_progress_storage(jetstream)
    return client, runs, requests, progress


@router.get("/capacity", response_model=CrawlConcurrencyLimits)
async def capacity() -> CrawlConcurrencyLimits:
    client = await connect_nats()
    try:
        jetstream = client.jetstream()
        _runs, _requests, workers_bucket = await ensure_graph_storage(jetstream)
        resource_grants = await ensure_resource_governor_storage(jetstream)
        catalogue_workers_bucket = await ensure_catalogue_worker_storage(jetstream)
        workers = sorted(
            await list_worker_states(workers_bucket), key=lambda value: value.worker_id
        )
        catalogue_workers = sorted(
            await list_catalogue_worker_states(catalogue_workers_bucket),
            key=lambda value: value.worker_id,
        )
        resources = await resource_usage(resource_grants)

        async def consumer_backlog(durable: str) -> int:
            try:
                info = await jetstream.consumer_info(WORK_STREAM, durable)
            except NotFoundError:
                return 0
            return int(info.num_pending or 0) + int(info.num_ack_pending or 0)

        catalogue_backlogs = {
            "ingestion": await consumer_backlog(INGESTION_DURABLE),
            "materialization": (
                await consumer_backlog(SCOPE_LIVE_DURABLE)
                + await consumer_backlog(SCOPE_BACKFILL_DURABLE)
            ),
        }
    finally:
        await client.drain()
    catalogue_executors = [
        CatalogueExecutorCapacity(
            capability=capability,
            worker_count=sum(
                worker.capability == capability and worker.healthy
                for worker in catalogue_workers
            ),
            capacity=sum(
                worker.capacity
                for worker in catalogue_workers
                if worker.capability == capability and worker.healthy
            ),
            active=sum(
                worker.active_operation_count
                for worker in catalogue_workers
                if worker.capability == capability and worker.healthy
            ),
            backlog=catalogue_backlogs[capability],
        )
        for capability in ("ingestion", "materialization")
    ]
    executor_capacity = sum(item.capacity for item in catalogue_executors)
    return CrawlConcurrencyLimits(
        worker_count=len(workers),
        runtime_capacity=sum(worker.capacity for worker in workers),
        runtime_active=sum(worker.active_request_count for worker in workers),
        resource_acquire_timeout_seconds=RESOURCE_ACQUIRE_TIMEOUT_SECONDS,
        resources=resources,
        workers=[
            RuntimeWorkerCapacity.model_validate(worker, from_attributes=True)
            for worker in workers
        ],
        catalogue_executors=catalogue_executors,
        tuning=RuntimeSizing(
            catalogue_max_concurrency=catalogue_max_concurrency(),
            effective_catalogue_concurrency=min(
                catalogue_max_concurrency(), executor_capacity
            ),
            object_io_max_concurrency=object_io_max_concurrency(),
            crawl_lanes_per_replica=CRAWL_ACQUISITION_LANES,
            catalogue_lanes_per_replica=1,
            graph_consumer_delivery_ceiling=GRAPH_CONSUMER_MAX_ACK_PENDING,
            duckdb_threads_per_executor=duckdb_threads(),
            duckdb_memory_limit_per_executor=duckdb_memory_limit(),
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
) -> GraphRunSubmission:
    try:
        run = await submit_graph_run(
            session,
            graph_id=graph_id,
            urls=payload.urls,
            catalogue_snapshot_resolver=control.latest_snapshot,
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
) -> GraphRunList:
    client, runs, _requests, progress = await _storage()
    try:
        items = [
            run
            for run in await list_graph_runs(runs)
            if run.graph_id == graph_id and run.status in {"queued", "running"}
        ]
        items.sort(key=lambda run: run.created_at, reverse=True)
        summaries = await _run_summaries(session, progress, items)
    finally:
        await client.drain()
    return GraphRunList(
        items=summaries,
        total=len(items),
    )


@router.get("/{run_id}", response_model=GraphRun)
async def get(run_id: UUID) -> GraphRun:
    client, runs, _requests, _progress = await _storage()
    try:
        run = await get_graph_run(runs, run_id)
    finally:
        await client.drain()
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"Graph run {run_id} was not found."
        )
    return run


@router.get("/{run_id}/failures", response_model=GraphRunFailureList)
async def failures(run_id: UUID) -> GraphRunFailureList:
    client, runs, requests, _progress = await _storage()
    try:
        run = await get_graph_run(runs, run_id)
        if run is None:
            raise HTTPException(
                status_code=404,
                detail=f"Graph run {run_id} was not found.",
            )
        failed_requests = [
            request
            for request in await list_crawl_requests(requests, graph_run_id=run_id)
            if request.status == "failed"
        ]
        results = await ensure_ingestion_results(client.jetstream())
        states = await asyncio.gather(
            *(
                get_ingestion_state(
                    results,
                    crawl_ingestion_request_id(request.id),
                )
                for request in failed_requests
            )
        )
    finally:
        await client.drain()
    items = [
        _failure_record(request, state)
        for request, state in zip(failed_requests, states, strict=True)
    ]
    items.sort(key=lambda item: item.captured_at, reverse=True)
    return GraphRunFailureList(items=items, total=len(items))


def _failure_record(
    request: CrawlRequest,
    state: IngestionState | None,
) -> GraphRunFailure:
    if state is not None:
        crawl = state.crawl
        return GraphRunFailure(
            crawl_id=crawl.crawl_id,
            requested_url=crawl.requested_url,
            final_url=crawl.final_url,
            status_code=crawl.status_code,
            failure_code=crawl.failure_code,
            failure_stage=crawl.failure_stage or request.failure_stage,
            failure_detail=crawl.failure_detail or request.error,
            captured_at=crawl.captured_at,
        )
    attempt = (
        request.acquisition_attempts_json[-1]
        if request.acquisition_attempts_json
        else {}
    )
    return GraphRunFailure(
        crawl_id=request.id,
        requested_url=request.url,
        final_url=_optional_string(attempt.get("final_url")),
        status_code=_optional_int(attempt.get("status_code")),
        failure_code=_optional_string(attempt.get("failure_code")),
        failure_stage=request.failure_stage,
        failure_detail=request.error,
        captured_at=request.updated_at,
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


@router.get("/", response_model=GraphRunList)
async def list_runs(
    session: Annotated[Session, Depends(get_session)],
) -> GraphRunList:
    client, runs, _requests, progress = await _storage()
    try:
        items = await list_graph_runs(runs)
        items.sort(key=lambda run: run.created_at, reverse=True)
        summaries = await _run_summaries(session, progress, items)
    finally:
        await client.drain()
    return GraphRunList(
        items=summaries,
        total=len(items),
    )


def _progress_value(entry):
    model = NodeProgress if ".node." in entry.key else EdgeProgress
    return model.model_validate_json(entry.value)


async def _run_events(request: Request, client, runs, progress, run: GraphRun):
    watcher = await progress.watch(f"{run.id.hex}.>")
    try:
        nodes: dict[str, object] = {}
        edges: dict[str, object] = {}
        snapshot_revision = 0
        while True:
            try:
                entry = await watcher.updates(timeout=10)
            except NatsTimeoutError:
                yield "event: heartbeat\ndata: {}\n\n"
                continue
            if entry is None:
                break
            if entry.key.endswith(".ready"):
                continue
            value = _progress_value(entry)
            snapshot_revision = max(snapshot_revision, entry.revision)
            target = nodes if isinstance(value, NodeProgress) else edges
            target[
                str(value.node_id if isinstance(value, NodeProgress) else value.edge_id)
            ] = value.model_dump(mode="json")
        payload = json.dumps(
            {"graph_run_id": str(run.id), "nodes": nodes, "edges": edges},
            separators=(",", ":"),
        )
        yield f"id: {snapshot_revision}\nevent: progress_snapshot\ndata: {payload}\n\n"
        current = await get_graph_run(runs, run.id) or run
        if current.status not in {"queued", "running"}:
            yield f"event: run_settled\ndata: {current.model_dump_json()}\n\n"
            return

        while not await request.is_disconnected():
            try:
                entry = await watcher.updates(timeout=10)
            except NatsTimeoutError:
                yield "event: heartbeat\ndata: {}\n\n"
                continue
            if entry is None:
                continue
            if entry.key.endswith(".ready"):
                continue
            value = _progress_value(entry)
            yield f"id: {entry.revision}\nevent: {value.kind}\ndata: {value.model_dump_json()}\n\n"
            if value.settled:
                current = await get_graph_run(runs, run.id) or run
                if current.status not in {"queued", "running"}:
                    yield f"event: run_settled\ndata: {current.model_dump_json()}\n\n"
                    return
    finally:
        await watcher.stop()
        await client.drain()


@router.get("/{run_id}/events")
async def run_events(run_id: UUID, request: Request) -> StreamingResponse:
    client, runs, requests, progress = await _storage()
    run = await get_graph_run(runs, run_id)
    if run is None:
        await client.drain()
        raise HTTPException(
            status_code=404, detail=f"Graph run {run_id} was not found."
        )
    await bootstrap_run_progress(progress, requests, run)
    return StreamingResponse(
        _run_events(request, client, runs, progress, run),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{run_id}/cancel", response_model=GraphRun)
async def cancel(run_id: UUID) -> GraphRun:
    client, runs, requests, progress = await _storage()
    try:
        return await request_cancellation(runs, requests, run_id, progress=progress)
    except GraphRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        await client.drain()
