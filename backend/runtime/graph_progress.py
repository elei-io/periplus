"""Current per-component graph progress derived from NATS runtime state."""

import asyncio
from collections import Counter
from dataclasses import dataclass
from time import monotonic
from typing import Literal
from uuid import UUID

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from nats.js.errors import KeyNotFoundError, KeyDeletedError, KeyWrongLastSequenceError

from .graph_queue import CrawlRequest, EdgeEvaluation, GraphRun, list_crawl_requests, list_edge_evaluations


@dataclass(frozen=True)
class _RunProgressSnapshot:
    captured_at: float
    requests: list[CrawlRequest]
    evaluations: list[EdgeEvaluation]


_snapshot_cache: dict[UUID, _RunProgressSnapshot] = {}
_snapshot_locks: dict[UUID, asyncio.Lock] = {}


async def _run_snapshot(bucket, run_id: UUID) -> _RunProgressSnapshot:
    cached = _snapshot_cache.get(run_id)
    if cached is not None and monotonic() - cached.captured_at < 0.5:
        return cached
    lock = _snapshot_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        cached = _snapshot_cache.get(run_id)
        if cached is not None and monotonic() - cached.captured_at < 0.5:
            return cached
        requests, evaluations = await asyncio.gather(
            list_crawl_requests(bucket, graph_run_id=run_id),
            list_edge_evaluations(bucket, graph_run_id=run_id),
        )
        snapshot = _RunProgressSnapshot(
            monotonic(),
            [request for request in requests if request.purpose == "use"],
            evaluations,
        )
        _snapshot_cache[run_id] = snapshot
        if len(_snapshot_cache) > 128:
            oldest = min(_snapshot_cache, key=lambda value: _snapshot_cache[value].captured_at)
            _snapshot_cache.pop(oldest, None)
            _snapshot_locks.pop(oldest, None)
        return snapshot


class NodeActivity(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: UUID
    status: str
    url: str
    updated_at: datetime
    error: str | None


class NodeProgress(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["node_progress"] = "node_progress"
    graph_run_id: UUID
    node_id: UUID
    admitted: int
    queued: int
    crawling: int
    awaiting_navigation: int
    evaluating_edges: int
    completed: int
    failed: int
    cancelled: int
    activity: tuple[NodeActivity, ...]
    settled: bool


class EdgeProgress(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["edge_progress"] = "edge_progress"
    graph_run_id: UUID
    edge_id: UUID
    evaluations_pending: int
    evaluations_running: int
    evaluations_completed: int
    evaluations_failed: int
    urls_selected: int
    urls_admitted: int
    urls_deduplicated: int
    settled: bool


def node_progress_key(run_id: UUID, node_id: UUID) -> str:
    return f"{run_id.hex}.node.{node_id.hex}"


def edge_progress_key(run_id: UUID, edge_id: UUID) -> str:
    return f"{run_id.hex}.edge.{edge_id.hex}"


def progress_ready_key(run_id: UUID) -> str:
    return f"{run_id.hex}.ready"


async def initialize_run_progress(bucket, run: GraphRun, *, mark_ready: bool = True) -> None:
    for node in run.snapshot.nodes:
        value = NodeProgress(
            graph_run_id=run.id,
            node_id=node.id,
            admitted=0,
            queued=0,
            crawling=0,
            awaiting_navigation=0,
            evaluating_edges=0,
            completed=0,
            failed=0,
            cancelled=0,
            activity=(),
            settled=False,
        )
        await _create_if_missing(bucket, node_progress_key(run.id, node.id), value)
    for edge in run.snapshot.edges:
        value = EdgeProgress(
            graph_run_id=run.id,
            edge_id=edge.id,
            evaluations_pending=0,
            evaluations_running=0,
            evaluations_completed=0,
            evaluations_failed=0,
            urls_selected=0,
            urls_admitted=0,
            urls_deduplicated=0,
            settled=False,
        )
        await _create_if_missing(bucket, edge_progress_key(run.id, edge.id), value)
    if mark_ready:
        await bucket.put(progress_ready_key(run.id), b"1")


async def bootstrap_run_progress(bucket, requests, run: GraphRun) -> None:
    """Seed projections once for runs created before projection support was active."""

    try:
        await bucket.get(progress_ready_key(run.id))
        return
    except (KeyNotFoundError, KeyDeletedError):
        pass
    await initialize_run_progress(bucket, run, mark_ready=False)
    for node in run.snapshot.nodes:
        value = await node_progress(requests, run, node.id)
        await bucket.put(node_progress_key(run.id, node.id), value.model_dump_json().encode())
    for edge in run.snapshot.edges:
        value = await edge_progress(requests, run, edge.id)
        await bucket.put(edge_progress_key(run.id, edge.id), value.model_dump_json().encode())
    await bucket.put(progress_ready_key(run.id), b"1")


async def _create_if_missing(bucket, key: str, value: BaseModel) -> None:
    try:
        await bucket.create(key, value.model_dump_json().encode())
    except KeyWrongLastSequenceError:
        return


async def _mutate(bucket, key: str, model, mutate):
    while True:
        try:
            entry = await bucket.get(key)
        except (KeyNotFoundError, KeyDeletedError):
            raise KeyError(key)
        current = model.model_validate_json(entry.value)
        updated = mutate(current)
        if updated == current:
            return current
        try:
            await bucket.update(key, updated.model_dump_json().encode(), last=entry.revision)
            return updated
        except KeyWrongLastSequenceError:
            continue


async def transition_node_progress(
    bucket,
    request: CrawlRequest,
    *,
    previous_status: str | None,
) -> NodeProgress:
    key = node_progress_key(request.graph_run_id, request.node_id)
    status_fields = {
        "queued", "crawling", "awaiting_navigation",
        "evaluating_edges", "completed", "failed", "cancelled",
    }
    if request.status not in status_fields or (previous_status is not None and previous_status not in status_fields):
        raise ValueError("unknown crawl request progress status")

    def apply(value: NodeProgress) -> NodeProgress:
        updates: dict[str, object] = {}
        if previous_status is None:
            updates["admitted"] = value.admitted + 1
        elif previous_status != request.status:
            updates[previous_status] = max(0, getattr(value, previous_status) - 1)
        if previous_status != request.status:
            updates[request.status] = getattr(value, request.status) + 1
            activity = NodeActivity(
                request_id=request.id,
                status=request.status,
                url=request.url,
                updated_at=request.updated_at,
                error=request.error,
            )
            updates["activity"] = (activity, *value.activity)[:3]
        return value.model_copy(update=updates)

    return await _mutate(bucket, key, NodeProgress, apply)


async def transition_edge_evaluation_progress(
    bucket,
    evaluation: EdgeEvaluation,
    *,
    previous_status: str | None,
) -> EdgeProgress:
    key = edge_progress_key(evaluation.graph_run_id, evaluation.edge_id)
    fields = {
        "pending": "evaluations_pending",
        "running": "evaluations_running",
        "completed": "evaluations_completed",
        "failed": "evaluations_failed",
    }

    def apply(value: EdgeProgress) -> EdgeProgress:
        updates: dict[str, int] = {}
        if previous_status is not None and previous_status != evaluation.status:
            field = fields[previous_status]
            updates[field] = max(0, getattr(value, field) - 1)
        if previous_status != evaluation.status:
            field = fields[evaluation.status]
            updates[field] = getattr(value, field) + 1
        return value.model_copy(update=updates)

    return await _mutate(bucket, key, EdgeProgress, apply)


async def add_edge_output_progress(
    bucket,
    run_id: UUID,
    edge_id: UUID,
    *,
    selected: int = 0,
    admitted: int = 0,
    deduplicated: int = 0,
) -> EdgeProgress:
    return await _mutate(
        bucket,
        edge_progress_key(run_id, edge_id),
        EdgeProgress,
        lambda value: value.model_copy(update={
            "urls_selected": value.urls_selected + selected,
            "urls_admitted": value.urls_admitted + admitted,
            "urls_deduplicated": value.urls_deduplicated + deduplicated,
        }),
    )


async def mark_run_progress_settled(bucket, run: GraphRun) -> None:
    for node in run.snapshot.nodes:
        await _mutate(
            bucket,
            node_progress_key(run.id, node.id),
            NodeProgress,
            lambda value: value.model_copy(update={"settled": True}),
        )
    for edge in run.snapshot.edges:
        await _mutate(
            bucket,
            edge_progress_key(run.id, edge.id),
            EdgeProgress,
            lambda value: value.model_copy(update={"settled": True}),
        )


async def node_progress(requests, run: GraphRun, node_id: UUID) -> NodeProgress:
    snapshot = await _run_snapshot(requests, run.id)
    values = [value for value in snapshot.requests if value.node_id == node_id]
    counts = Counter(value.status for value in values)
    activity = sorted(
        values,
        key=lambda value: value.updated_at,
        reverse=True,
    )[:3]
    return NodeProgress(
        graph_run_id=run.id,
        node_id=node_id,
        admitted=len(values),
        queued=counts["queued"],
        crawling=counts["crawling"],
        awaiting_navigation=counts["awaiting_navigation"],
        evaluating_edges=counts["evaluating_edges"],
        completed=counts["completed"],
        failed=counts["failed"],
        cancelled=counts["cancelled"],
        activity=tuple(
            NodeActivity(
                request_id=value.id,
                status=value.status,
                url=value.url,
                updated_at=value.updated_at,
                error=value.error,
            )
            for value in activity
        ),
        settled=run.status in {"completed", "completed_with_errors", "failed", "cancelled"},
    )


async def edge_progress(requests, run: GraphRun, edge_id: UUID) -> EdgeProgress:
    snapshot = await _run_snapshot(requests, run.id)
    evaluations = [value for value in snapshot.evaluations if value.edge_id == edge_id]
    evaluation_counts = Counter(value.status for value in evaluations)
    crawl_requests = snapshot.requests
    selected = sum(value.output_count for value in evaluations)
    admitted = sum(value.source_edge_id == edge_id for value in crawl_requests)
    return EdgeProgress(
        graph_run_id=run.id,
        edge_id=edge_id,
        evaluations_pending=evaluation_counts["pending"],
        evaluations_running=evaluation_counts["running"],
        evaluations_completed=evaluation_counts["completed"],
        evaluations_failed=evaluation_counts["failed"],
        urls_selected=selected,
        urls_admitted=admitted,
        urls_deduplicated=max(0, selected - admitted),
        settled=run.status in {"completed", "completed_with_errors", "failed", "cancelled"},
    )
