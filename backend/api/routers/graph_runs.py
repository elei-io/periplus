"""Current crawl-graph run API."""

import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from nats.errors import TimeoutError as NatsTimeoutError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_float, get_int
from control.catalogue_materializations.models import CatalogueMaterialization
from control.crawl_graphs.models import CrawlGraph
from control.crawl_graphs.service import CrawlGraphNotFoundError, CrawlGraphValidationError, freeze_graph
from db.session import get_session
from repository.catalogue import Catalogue, catalogue_from_env
from runtime.graph_queue import GraphRun, connect_nats, ensure_graph_progress_storage, ensure_graph_storage, get_graph_run, list_graph_runs, list_worker_states
from runtime.graph_runs import (
    GraphRunNotFoundError,
    create_graph_run,
    request_cancellation,
    resolve_policy_snapshot,
)
from runtime.graph_progress import EdgeProgress, NodeProgress, bootstrap_run_progress

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
    graph_name: str | None
    status: Literal["queued", "running", "completed", "completed_with_errors", "failed", "cancelled"]
    trigger_kind: Literal["manual"]
    trigger_urls: tuple[str, ...]
    request_count: int
    pending_request_count: int
    failed_request_count: int
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


class CrawlConcurrencyLimits(BaseModel):
    worker_count: int
    runtime_capacity: int
    runtime_active: int
    browser_concurrency_per_worker: int
    browser_capacity: int
    crawl_permit_timeout_seconds: float
    workers: list[RuntimeWorkerCapacity]


class GraphRunMaterializationLag(BaseModel):
    run_id: UUID
    materialization_count: int
    pending_updates: int
    failed_updates: int


class GraphRunMaterializationLagList(BaseModel):
    items: list[GraphRunMaterializationLag]


def _run_summaries(session: Session, runs: list[GraphRun]) -> list[GraphRunSummary]:
    graph_ids = {run.graph_id for run in runs}
    names = dict(
        session.execute(
            select(CrawlGraph.id, CrawlGraph.name).where(CrawlGraph.id.in_(graph_ids))
        ).all()
    ) if graph_ids else {}
    return [
        GraphRunSummary(
            **run.model_dump(),
            graph_name=names.get(run.graph_id),
        )
        for run in runs
    ]


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
        _runs, _requests, workers_bucket = await ensure_graph_storage(client.jetstream())
        workers = sorted(await list_worker_states(workers_bucket), key=lambda value: value.worker_id)
    finally:
        await client.drain()
    browser_per_worker = get_int("ATLAS_BROWSER_CONCURRENCY")
    return CrawlConcurrencyLimits(
        worker_count=len(workers),
        runtime_capacity=sum(worker.capacity for worker in workers),
        runtime_active=sum(worker.active_request_count for worker in workers),
        browser_concurrency_per_worker=browser_per_worker,
        browser_capacity=len(workers) * browser_per_worker,
        crawl_permit_timeout_seconds=get_float("ATLAS_CRAWL_PERMIT_TIMEOUT_SECONDS"),
        workers=[RuntimeWorkerCapacity.model_validate(worker, from_attributes=True) for worker in workers],
    )


@router.get(
    "/materialization-lag", response_model=GraphRunMaterializationLagList
)
def materialization_lag(
    session: Annotated[Session, Depends(get_session)],
) -> GraphRunMaterializationLagList:
    active_definitions = list(
        session.execute(
            select(
                CatalogueMaterialization.id,
                CatalogueMaterialization.definition_revision_id,
            ).where(CatalogueMaterialization.archived_at.is_(None))
        ).all()
    )
    with catalogue_from_env() as catalogue:
        rows = _graph_run_materialization_lag_rows(catalogue, active_definitions)
    items: list[GraphRunMaterializationLag] = []
    for row in rows:
        items.append(
            GraphRunMaterializationLag(
                run_id=UUID(str(row[0])),
                materialization_count=int(row[1]),
                pending_updates=int(row[2]),
                failed_updates=int(row[3]),
            )
        )
    return GraphRunMaterializationLagList(items=items)


def _graph_run_materialization_lag_rows(
    catalogue: Catalogue, active_definitions: list[tuple[UUID, UUID]]
) -> list[tuple]:
    crawls = _catalogue_table(catalogue, "crawls")
    members = _catalogue_table(catalogue, "crawl_materialization_fanout_members")
    active_filter = "FALSE"
    parameters: list[object] = []
    if active_definitions:
        active_filter = " OR ".join(
            "(m.materialization_id = ? AND m.definition_revision_id = ?)"
            for _ in active_definitions
        )
        parameters = [value for pair in active_definitions for value in pair]
    return catalogue.connection.execute(
        f"""
        SELECT c.graph_run_id,
               count(DISTINCT m.materialization_id) AS materialization_count,
               count(DISTINCT (m.materialization_id, m.scope_kind, m.scope_id))
                   FILTER (WHERE m.status = 'planned') AS pending_updates,
               count(DISTINCT (m.materialization_id, m.scope_kind, m.scope_id))
                   FILTER (WHERE m.status = 'failed') AS failed_updates
        FROM {crawls} AS c
        LEFT JOIN {members} AS m
          ON m.crawl_id = c.crawl_id
         AND ({active_filter})
        GROUP BY c.graph_run_id
        ORDER BY max(c.captured_at) DESC
        """,
        parameters,
    ).fetchall()


def _catalogue_table(catalogue: Catalogue, name: str) -> str:
    return ".".join(
        '"' + value.replace('"', '""') + '"'
        for value in (catalogue.config.alias, catalogue.config.schema, name)
    )


@trigger_router.post("/{graph_id}/runs", response_model=GraphRunSubmission, status_code=202)
async def trigger(
    graph_id: UUID,
    payload: GraphRunTrigger,
    session: Annotated[Session, Depends(get_session)],
) -> GraphRunSubmission:
    try:
        snapshot = freeze_graph(session, graph_id)
    except CrawlGraphNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CrawlGraphValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Component immutability is authoritative before the NATS execution snapshot is published.
    session.commit()
    client, runs, requests, progress = await _storage()
    try:
        run = await create_graph_run(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=client.jetstream(),
            snapshot=snapshot,
            urls=payload.urls,
            policy_resolver=lambda url: resolve_policy_snapshot(session, url),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await client.drain()
    return GraphRunSubmission(graph_id=graph_id, run_id=run.id)


@trigger_router.get("/{graph_id}/runs/active", response_model=GraphRunList)
async def active_graph_runs(
    graph_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> GraphRunList:
    client, runs, _requests, _progress = await _storage()
    try:
        items = [
            run
            for run in await list_graph_runs(runs)
            if run.graph_id == graph_id and run.status in {"queued", "running"}
        ]
    finally:
        await client.drain()
    items.sort(key=lambda run: run.created_at, reverse=True)
    return GraphRunList(
        items=_run_summaries(session, items),
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
        raise HTTPException(status_code=404, detail=f"Graph run {run_id} was not found.")
    return run


@router.get("/", response_model=GraphRunList)
async def list_runs(
    session: Annotated[Session, Depends(get_session)],
) -> GraphRunList:
    client, runs, _requests, _progress = await _storage()
    try:
        items = await list_graph_runs(runs)
    finally:
        await client.drain()
    items.sort(key=lambda run: run.created_at, reverse=True)
    return GraphRunList(
        items=_run_summaries(session, items),
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
            target[str(value.node_id if isinstance(value, NodeProgress) else value.edge_id)] = value.model_dump(mode="json")
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
        raise HTTPException(status_code=404, detail=f"Graph run {run_id} was not found.")
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
