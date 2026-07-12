"""JetStream work and KV-backed current crawl-graph execution state."""

from __future__ import annotations

import hashlib
import asyncio
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import nats
from config import get_float, get_int, get_str
from nats.js.api import AckPolicy, ConsumerConfig, KeyValueConfig, RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import BadRequestError, BucketNotFoundError, KeyDeletedError, KeyNotFoundError, KeyWrongLastSequenceError, NoKeysError, NotFoundError
from pydantic import BaseModel, ConfigDict
from control.crawl_graphs.schemas import EdgeDedupeMode, FrozenGraphEdge, FrozenGraphNode, FrozenGraphSnapshot

GRAPH_STREAM = "ATLAS_GRAPH_WORK"
CRAWL_SUBJECT = "atlas.graph.crawl"
EDGE_SUBJECT = "atlas.graph.edge"
READINESS_SUBJECT = "atlas.graph.readiness"
CRAWL_CONSUMER = "atlas-graph-crawl-workers"
EDGE_CONSUMER = "atlas-graph-edge-workers"
READINESS_CONSUMER = "atlas-graph-readiness-workers"
RUNS_BUCKET = "atlas_graph_runs"
REQUESTS_BUCKET = "atlas_crawl_requests"
WORKERS_BUCKET = "atlas_graph_workers"
PROGRESS_BUCKET = "atlas_graph_progress"

GraphRunStatus = Literal["queued", "running", "completed", "completed_with_errors", "failed", "cancelled"]
CrawlRequestStatus = Literal["queued", "crawling", "awaiting_ingestion", "awaiting_materializations", "evaluating_edges", "completed", "failed", "cancelled"]
TriggerKind = Literal["manual", "scheduled"]


class GraphRun(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    graph_id: UUID
    trigger_kind: TriggerKind
    status: GraphRunStatus = "queued"
    snapshot: FrozenGraphSnapshot
    trigger_urls: tuple[str, ...]
    seen_request_identities: tuple[str, ...] = ()
    request_count: int = 0
    pending_request_count: int = 0
    failed_request_count: int = 0
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    error: str | None = None


class CrawlRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    graph_run_id: UUID
    node_id: UUID
    url: str
    document_id: str | None = None
    effective_policy_snapshot_json: dict | None = None
    source_crawl_id: UUID | None = None
    source_edge_id: UUID | None = None
    parent_request_id: UUID | None = None
    status: CrawlRequestStatus = "queued"
    created_at: datetime
    updated_at: datetime
    error: str | None = None


class CrawlWork(BaseModel):
    model_config = ConfigDict(frozen=True)
    crawl_request_id: UUID


class EdgeWork(BaseModel):
    model_config = ConfigDict(frozen=True)
    graph_run_id: UUID
    crawl_request_id: UUID
    crawl_id: UUID
    edge_id: UUID


class EdgeEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)
    identity: str
    graph_run_id: UUID
    crawl_request_id: UUID
    crawl_id: UUID
    edge_id: UUID
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    created_at: datetime
    updated_at: datetime
    output_count: int = 0
    error: str | None = None


class ReadinessWork(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_id: UUID
    crawl_id: UUID
    graph_run_id: UUID
    crawl_request_id: UUID
    status: Literal["ready", "failed"]
    failed_materialization_ids: tuple[UUID, ...] = ()
    occurred_at: datetime


class WorkerState(BaseModel):
    model_config = ConfigDict(frozen=True)
    worker_id: str
    started_at: datetime
    last_seen_at: datetime
    capacity: int
    active_request_count: int
    stopping: bool = False


def normalize_request_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Crawl URL must be absolute HTTP(S): {url}")
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    port = parsed.port
    netloc = host if port is None or (scheme, port) in {("http", 80), ("https", 443)} else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def request_identity(
    graph_run_id: UUID,
    url: str,
    *,
    dedupe_mode: EdgeDedupeMode = EdgeDedupeMode.graph,
    source_edge_id: UUID | None = None,
    source_crawl_id: UUID | None = None,
    source_document_id: str | None = None,
) -> str:
    normalized = normalize_request_url(url)
    if dedupe_mode == EdgeDedupeMode.graph:
        value = f"{graph_run_id}:graph:{normalized}"
    elif dedupe_mode == EdgeDedupeMode.crawl:
        if source_edge_id is None or source_crawl_id is None:
            raise ValueError("Crawl deduplication requires a source edge and crawl.")
        value = f"{graph_run_id}:crawl:{source_edge_id}:{source_crawl_id}:{normalized}"
    else:
        if source_edge_id is None or source_document_id is None:
            raise ValueError("Document deduplication requires a source edge and document.")
        value = f"{graph_run_id}:document:{source_edge_id}:{source_document_id}:{normalized}"
    return hashlib.sha256(value.encode()).hexdigest()


def edge_evaluation_identity(graph_run_id: UUID, crawl_request_id: UUID, crawl_id: UUID, edge_id: UUID) -> str:
    return hashlib.sha256(f"{graph_run_id}:{crawl_request_id}:{crawl_id}:{edge_id}".encode()).hexdigest()


def new_graph_run(snapshot: FrozenGraphSnapshot, urls: list[str], trigger_kind: TriggerKind = "manual", now: datetime | None = None) -> GraphRun:
    now = now or datetime.now(UTC)
    normalized = tuple(normalize_request_url(url) for url in urls)
    if not normalized:
        raise ValueError("A graph run requires at least one URL.")
    if not any(node.id == snapshot.root_node_id for node in snapshot.nodes):
        raise ValueError("A graph run requires a valid root node.")
    return GraphRun(id=uuid4(), graph_id=snapshot.graph_id, trigger_kind=trigger_kind, snapshot=snapshot, trigger_urls=normalized, created_at=now)


async def connect_nats():
    return await nats.connect(get_str("NATS_URL"), connect_timeout=2, max_reconnect_attempts=-1)


async def _bucket(jetstream, config: KeyValueConfig):
    try:
        return await jetstream.key_value(config.bucket)
    except BucketNotFoundError:
        try:
            return await jetstream.create_key_value(config=config)
        except BadRequestError:
            return await jetstream.key_value(config.bucket)


async def ensure_graph_storage(jetstream):
    replicas = get_int("ATLAS_GRAPH_STREAM_REPLICAS")
    stream = StreamConfig(name=GRAPH_STREAM, subjects=[CRAWL_SUBJECT, EDGE_SUBJECT, READINESS_SUBJECT], retention=RetentionPolicy.WORK_QUEUE, storage=StorageType.FILE, num_replicas=replicas)
    try:
        await jetstream.stream_info(GRAPH_STREAM)
    except NotFoundError:
        await jetstream.add_stream(config=stream)
    ack_wait = get_float("ATLAS_GRAPH_ACK_WAIT_SECONDS")
    pending = get_int("ATLAS_CRAWL_WORKER_CONCURRENCY")
    for durable, subject in ((CRAWL_CONSUMER, CRAWL_SUBJECT), (EDGE_CONSUMER, EDGE_SUBJECT), (READINESS_CONSUMER, READINESS_SUBJECT)):
        await jetstream.add_consumer(GRAPH_STREAM, config=ConsumerConfig(durable_name=durable, ack_policy=AckPolicy.EXPLICIT, ack_wait=ack_wait, filter_subject=subject, max_ack_pending=pending, max_deliver=-1))
    state_bytes = get_int("ATLAS_GRAPH_STATE_MAX_BYTES")
    runs = await _bucket(jetstream, KeyValueConfig(bucket=RUNS_BUCKET, description="Current Atlas graph-run state", history=1, max_bytes=state_bytes, storage=StorageType.FILE, replicas=replicas))
    requests = await _bucket(jetstream, KeyValueConfig(bucket=REQUESTS_BUCKET, description="Current Atlas crawl-request state", history=1, max_bytes=state_bytes, storage=StorageType.FILE, replicas=replicas))
    workers = await _bucket(jetstream, KeyValueConfig(bucket=WORKERS_BUCKET, description="Ephemeral Atlas crawl-worker presence", history=1, ttl=get_float("ATLAS_CRAWL_WORKER_PRESENCE_TTL_SECONDS"), storage=StorageType.FILE, replicas=replicas))
    return runs, requests, workers


async def ensure_graph_progress_storage(jetstream):
    return await _bucket(
        jetstream,
        KeyValueConfig(
            bucket=PROGRESS_BUCKET,
            description="Current per-component Atlas graph progress",
            history=1,
            max_bytes=get_int("ATLAS_GRAPH_STATE_MAX_BYTES"),
            storage=StorageType.FILE,
            replicas=get_int("ATLAS_GRAPH_STREAM_REPLICAS"),
        ),
    )


async def _get(bucket, key: str, model):
    try:
        entry = await bucket.get(key)
    except (KeyNotFoundError, KeyDeletedError):
        return None
    return model.model_validate_json(entry.value)


async def get_graph_run(bucket, run_id: UUID) -> GraphRun | None:
    return await _get(bucket, run_id.hex, GraphRun)


async def get_crawl_request(bucket, request_id: UUID) -> CrawlRequest | None:
    return await _get(bucket, request_id.hex, CrawlRequest)


def edge_evaluation_key(identity: str) -> str:
    return f"edge-{identity}"


async def get_edge_evaluation(bucket, identity: str) -> EdgeEvaluation | None:
    return await _get(bucket, edge_evaluation_key(identity), EdgeEvaluation)


async def _update(bucket, key: str, model, mutate):
    while True:
        try:
            entry = await bucket.get(key)
        except (KeyNotFoundError, KeyDeletedError) as exc:
            raise KeyError(key) from exc
        current = model.model_validate_json(entry.value)
        updated = mutate(current)
        if updated is current or updated == current:
            return current
        try:
            await bucket.update(key, updated.model_dump_json().encode(), last=entry.revision)
            return updated
        except KeyWrongLastSequenceError:
            continue


async def update_graph_run(bucket, run_id: UUID, mutate) -> GraphRun:
    return await _update(bucket, run_id.hex, GraphRun, mutate)


async def update_crawl_request(bucket, request_id: UUID, mutate) -> CrawlRequest:
    return await _update(bucket, request_id.hex, CrawlRequest, mutate)


async def update_edge_evaluation(bucket, identity: str, mutate) -> EdgeEvaluation:
    return await _update(bucket, edge_evaluation_key(identity), EdgeEvaluation, mutate)


async def list_graph_runs(bucket) -> list[GraphRun]:
    try:
        keys = await bucket.keys()
    except (KeyNotFoundError, KeyDeletedError, NoKeysError):
        return []
    return [value for key in keys if (value := await _get(bucket, key, GraphRun)) is not None]


async def list_worker_states(bucket) -> list[WorkerState]:
    try:
        keys = await bucket.keys()
    except (KeyNotFoundError, KeyDeletedError, NoKeysError):
        return []
    return [
        value
        for key in keys
        if (value := await _get(bucket, key, WorkerState)) is not None
    ]


async def list_crawl_requests(bucket, *, graph_run_id: UUID | None = None) -> list[CrawlRequest]:
    try:
        keys = await bucket.keys()
    except (KeyNotFoundError, KeyDeletedError, NoKeysError):
        return []
    request_keys = [key for key in keys if not key.startswith("edge-")]
    values: list[CrawlRequest] = []
    for start in range(0, len(request_keys), 64):
        batch = await asyncio.gather(
            *(_get(bucket, key, CrawlRequest) for key in request_keys[start : start + 64])
        )
        values.extend(
            value for value in batch
            if value is not None and (graph_run_id is None or value.graph_run_id == graph_run_id)
        )
    return values


async def list_edge_evaluations(bucket, *, graph_run_id: UUID | None = None) -> list[EdgeEvaluation]:
    try:
        keys = await bucket.keys()
    except (KeyNotFoundError, KeyDeletedError, NoKeysError):
        return []
    evaluation_keys = [key for key in keys if key.startswith("edge-")]
    values: list[EdgeEvaluation] = []
    for start in range(0, len(evaluation_keys), 64):
        batch = await asyncio.gather(
            *(_get(bucket, key, EdgeEvaluation) for key in evaluation_keys[start : start + 64])
        )
        values.extend(
            value for value in batch
            if value is not None and (graph_run_id is None or value.graph_run_id == graph_run_id)
        )
    return values


async def publish_crawl(jetstream, request_id: UUID) -> None:
    await jetstream.publish(CRAWL_SUBJECT, CrawlWork(crawl_request_id=request_id).model_dump_json().encode(), stream=GRAPH_STREAM, headers={"Nats-Msg-Id": request_id.hex})


async def publish_edge(jetstream, work: EdgeWork) -> None:
    identity = edge_evaluation_identity(work.graph_run_id, work.crawl_request_id, work.crawl_id, work.edge_id)
    await jetstream.publish(EDGE_SUBJECT, work.model_dump_json().encode(), stream=GRAPH_STREAM, headers={"Nats-Msg-Id": identity})
