"""JetStream graph-work contracts and Postgres runtime read models."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from config import get_float, get_int
from config.performance import GRAPH_ACK_WAIT_SECONDS, GRAPH_CONSUMER_MAX_ACK_PENDING
from nats.js.api import AckPolicy, ConsumerConfig, KeyValueConfig, RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import BadRequestError, BucketNotFoundError, KeyDeletedError, KeyNotFoundError, NotFoundError
from pydantic import BaseModel, ConfigDict, Field
from control.crawl_graphs.schemas import (
    DEFAULT_GRAPH_RUN_MAX_CRAWLS,
    MAX_GRAPH_RUN_CRAWLS,
    EdgeDedupeMode,
    FrozenGraphEdge,
    FrozenGraphNode,
    FrozenGraphSnapshot,
)
from control.crawl_policies.schemas import CrawlPolicySnapshot
from control.urls import normalize_url
from runtime.navigation_contract import EdgeSelectionPackage, NavigationPackage

GRAPH_STREAM = "ATLAS_GRAPH_WORK"
CRAWL_SUBJECT = "atlas.graph.crawl"
EDGE_SUBJECT = "atlas.graph.edge"
NAVIGATION_READINESS_SUBJECT = "atlas.graph.navigation.readiness"
CRAWL_CONSUMER = "atlas-graph-crawl-workers"
EDGE_CONSUMER = "atlas-graph-edge-workers"
NAVIGATION_READINESS_CONSUMER = "atlas-graph-navigation-readiness-workers"
WORKERS_BUCKET = "atlas_graph_workers"

GraphRunStatus = Literal[
    "queued",
    "running",
    "paused",
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
]
CrawlRequestStatus = Literal["queued", "crawling", "awaiting_navigation", "evaluating_edges", "completed", "failed", "cancelled"]
CatalogueConsistency = Literal["run_frozen"]
FailureStage = Literal[
    "admission",
    "connection",
    "request",
    "navigation",
    "completion",
    "acquisition",
    "edge",
    "lifecycle",
]
TriggerKind = Literal["manual", "schedule"]
MAX_GRAPH_RUN_FAILURE_GROUPS = 32


class GraphRunFailureGroup(BaseModel):
    """Bounded operational summary for one terminal crawl failure type."""

    model_config = ConfigDict(frozen=True)
    failure_stage: str
    failure_code: str
    status_code: int | None = None
    count: int = Field(ge=1)
    example_url: str
    example_detail: str | None = None
    last_occurred_at: datetime


class GraphRun(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    graph_id: UUID
    trigger_kind: TriggerKind
    trigger_schedule_id: UUID | None = None
    catalogue_snapshot_id: int | None = Field(default=None, ge=0)
    catalogue_consistency: CatalogueConsistency = "run_frozen"
    generation: int = Field(default=1, ge=1)
    status: GraphRunStatus = "queued"
    snapshot: FrozenGraphSnapshot
    trigger_urls: tuple[str, ...]
    max_crawls: int = Field(default=DEFAULT_GRAPH_RUN_MAX_CRAWLS, ge=1, le=MAX_GRAPH_RUN_CRAWLS)
    crawl_limit_reached: bool = False
    root_admission_cursor: int = Field(default=0, ge=0)
    request_count: int = 0
    pending_request_count: int = 0
    acquisition_pending_count: int = Field(default=0, ge=0)
    failed_request_count: int = 0
    error_count: int = 0
    failure_groups: tuple[GraphRunFailureGroup, ...] = ()
    created_at: datetime
    started_at: datetime | None = None
    last_progress_at: datetime | None = None
    completed_at: datetime | None = None
    paused_at: datetime | None = None
    not_before: datetime | None = None
    deadline_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    error: str | None = None


class CrawlRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    graph_run_id: UUID
    node_id: UUID
    url: str
    document_id: str | None = None
    effective_policy_snapshot_json: dict
    source_crawl_id: UUID | None = None
    source_edge_id: UUID | None = None
    parent_request_id: UUID | None = None
    status: CrawlRequestStatus = "queued"
    generation: int = Field(default=1, ge=1)
    priority: int = 0
    not_before: datetime | None = None
    claim_token: UUID | None = None
    claim_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    failure_stage: FailureStage | None = None
    processing_failure_count: int = Field(default=0, ge=0)
    acquisition_attempts_json: tuple[dict, ...] = ()


class CrawlWork(BaseModel):
    model_config = ConfigDict(frozen=True)
    crawl_request_id: UUID
    generation: int = Field(default=1, ge=1)


class EdgeWork(BaseModel):
    model_config = ConfigDict(frozen=True)
    graph_run_id: UUID
    crawl_request_id: UUID
    crawl_id: UUID
    edge_id: UUID
    generation: int = Field(default=1, ge=1)
    navigation: NavigationPackage
    catalogue_snapshot_id: int | None = Field(default=None, ge=0)


class EdgeEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)
    identity: str
    graph_run_id: UUID
    crawl_request_id: UUID
    crawl_id: UUID
    edge_id: UUID
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    claim_token: UUID | None = None
    claim_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    output_count: int = 0
    selection: EdgeSelectionPackage | None = None
    error: str | None = None


class NavigationReadinessWork(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_id: UUID
    crawl_id: UUID
    graph_run_id: UUID
    crawl_request_id: UUID
    generation: int = Field(default=1, ge=1)
    navigation: NavigationPackage | None = None
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
    try:
        return normalize_url(url)
    except ValueError as exc:
        raise ValueError(f"Crawl URL must be absolute HTTP(S): {url}") from exc


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


def new_graph_run(
    snapshot: FrozenGraphSnapshot,
    urls: list[str],
    trigger_kind: TriggerKind = "manual",
    now: datetime | None = None,
    *,
    run_id: UUID | None = None,
    trigger_schedule_id: UUID | None = None,
    catalogue_snapshot_id: int | None = None,
    max_crawls: int = DEFAULT_GRAPH_RUN_MAX_CRAWLS,
    max_run_seconds: int | None = None,
) -> GraphRun:
    now = now or datetime.now(UTC)
    normalized = tuple(dict.fromkeys(normalize_request_url(url) for url in urls))
    if not normalized:
        raise ValueError("A graph run requires at least one URL.")
    if not any(node.id == snapshot.root_node_id for node in snapshot.nodes):
        raise ValueError("A graph run requires a valid root node.")
    if not 1 <= max_crawls <= MAX_GRAPH_RUN_CRAWLS:
        raise ValueError(
            f"A graph run maximum crawl budget must be between 1 and {MAX_GRAPH_RUN_CRAWLS}."
        )
    if max_crawls < len(normalized):
        raise ValueError(
            "A graph run maximum crawl budget cannot be smaller than its number "
            f"of distinct root URLs ({len(normalized)})."
        )
    return GraphRun(
        id=run_id or uuid4(),
        graph_id=snapshot.graph_id,
        trigger_kind=trigger_kind,
        trigger_schedule_id=trigger_schedule_id,
        catalogue_snapshot_id=catalogue_snapshot_id,
        snapshot=snapshot,
        trigger_urls=normalized,
        max_crawls=max_crawls,
        created_at=now,
        deadline_at=(
            now + timedelta(seconds=max_run_seconds)
            if max_run_seconds is not None
            else None
        ),
    )


async def _bucket(jetstream, config: KeyValueConfig):
    desired_max_bytes = config.max_bytes
    if desired_max_bytes is None or desired_max_bytes <= 0:
        raise ValueError(f"JetStream KV {config.bucket} requires positive max_bytes")
    try:
        bucket = await jetstream.key_value(config.bucket)
    except BucketNotFoundError:
        try:
            bucket = await jetstream.create_key_value(config=config)
        except BadRequestError:
            bucket = await jetstream.key_value(config.bucket)
    status = await bucket.status()
    actual = status.stream_info.config
    if (
        actual.max_msgs_per_subject != config.history
        or actual.storage != config.storage
        or actual.num_replicas != config.replicas
    ):
        raise RuntimeError(
            f"JetStream KV {config.bucket} has a superseded storage contract; "
            "reset disposable NATS state before starting Atlas"
        )
    desired_ttl = float(config.ttl or 0)
    updates = {}
    if actual.max_age != desired_ttl:
        updates["max_age"] = desired_ttl
    if actual.max_bytes != desired_max_bytes:
        updates["max_bytes"] = desired_max_bytes
    if updates:
        await jetstream.update_stream(config=actual.evolve(**updates))
    return bucket


async def ensure_graph_storage(jetstream):
    replicas = get_int("ATLAS_GRAPH_STREAM_REPLICAS")
    subjects = [CRAWL_SUBJECT, EDGE_SUBJECT, NAVIGATION_READINESS_SUBJECT]
    max_bytes = get_int("ATLAS_GRAPH_WORK_MAX_BYTES")
    stream = StreamConfig(
        name=GRAPH_STREAM,
        subjects=subjects,
        retention=RetentionPolicy.WORK_QUEUE,
        storage=StorageType.FILE,
        num_replicas=replicas,
        max_bytes=max_bytes,
    )
    try:
        info = await jetstream.stream_info(GRAPH_STREAM)
    except NotFoundError:
        await jetstream.add_stream(config=stream)
    else:
        if (
            set(info.config.subjects) != set(subjects)
            or info.config.retention != RetentionPolicy.WORK_QUEUE
            or info.config.storage != StorageType.FILE
            or info.config.num_replicas != replicas
            or info.config.max_bytes != max_bytes
        ):
            raise RuntimeError(
                f"JetStream {GRAPH_STREAM} has the superseded graph-work contract; "
                "reset disposable NATS state before starting Atlas"
            )
    ack_wait = GRAPH_ACK_WAIT_SECONDS
    consumers = (
        (CRAWL_CONSUMER, CRAWL_SUBJECT, GRAPH_CONSUMER_MAX_ACK_PENDING),
        (EDGE_CONSUMER, EDGE_SUBJECT, GRAPH_CONSUMER_MAX_ACK_PENDING),
        (
            NAVIGATION_READINESS_CONSUMER,
            NAVIGATION_READINESS_SUBJECT,
            GRAPH_CONSUMER_MAX_ACK_PENDING,
        ),
    )
    for durable, subject, pending in consumers:
        expected = ConsumerConfig(
            durable_name=durable,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=ack_wait,
            filter_subject=subject,
            max_ack_pending=pending,
            max_deliver=-1,
        )
        consumer = await _ensure_consumer(jetstream, expected)
        actual = consumer.config
        if (
            actual.ack_policy != AckPolicy.EXPLICIT
            or actual.ack_wait != ack_wait
            or actual.filter_subject != subject
            or actual.max_ack_pending != pending
            or actual.max_deliver != -1
        ):
            raise RuntimeError(
                f"JetStream consumer {durable} has the superseded graph-work contract; "
                "reset disposable NATS state before starting Atlas"
            )
    workers = await _bucket(
        jetstream,
        KeyValueConfig(
            bucket=WORKERS_BUCKET,
            description="Ephemeral Atlas acquisition-worker presence",
            history=1,
            ttl=get_float("ATLAS_ACQUISITION_WORKER_PRESENCE_TTL_SECONDS"),
            max_bytes=get_int("ATLAS_GRAPH_WORKER_MAX_BYTES"),
            storage=StorageType.FILE,
            replicas=replicas,
        ),
    )
    from .graph_store import AsyncGraphRuntimeStore

    runtime = AsyncGraphRuntimeStore()
    return runtime, runtime, workers


async def _ensure_consumer(jetstream, expected: ConsumerConfig):
    durable = expected.durable_name
    if durable is None:
        raise ValueError("graph consumer requires a durable name")
    try:
        return await jetstream.consumer_info(GRAPH_STREAM, durable)
    except NotFoundError:
        try:
            return await jetstream.add_consumer(
                GRAPH_STREAM,
                config=expected,
            )
        except BadRequestError:
            # Another process may have created the durable after our lookup.
            # Attach to it instead of reconfiguring it.
            return await jetstream.consumer_info(GRAPH_STREAM, durable)


async def _get(bucket, key: str, model):
    try:
        entry = await bucket.get(key)
    except (KeyNotFoundError, KeyDeletedError):
        return None
    return model.model_validate_json(entry.value)


async def get_graph_run(bucket, run_id: UUID) -> GraphRun | None:
    return await bucket.get_run(run_id)


async def get_crawl_request(bucket, request_id: UUID) -> CrawlRequest | None:
    return await bucket.get_request(request_id)


async def get_edge_evaluation(bucket, identity: str) -> EdgeEvaluation | None:
    return await bucket.get_edge_evaluation(identity)


async def update_graph_run(bucket, run_id: UUID, mutate) -> GraphRun:
    return await bucket.update_run(run_id, mutate)


async def update_crawl_request(bucket, request_id: UUID, mutate) -> CrawlRequest:
    return await bucket.update_request(request_id, mutate)


async def update_edge_evaluation(bucket, identity: str, mutate) -> EdgeEvaluation:
    return await bucket.update_edge_evaluation(identity, mutate)


async def list_graph_runs(bucket) -> list[GraphRun]:
    return await bucket.list_runs()


async def list_worker_states(bucket) -> list[WorkerState]:
    keys = await _list_keys(bucket)
    return [
        value
        for key in keys
        if (value := await _get(bucket, key, WorkerState)) is not None
    ]


async def list_crawl_requests(bucket, *, graph_run_id: UUID | None = None) -> list[CrawlRequest]:
    return await bucket.list_requests(graph_run_id=graph_run_id)


async def list_edge_evaluations(bucket, *, graph_run_id: UUID | None = None) -> list[EdgeEvaluation]:
    return await bucket.list_edge_evaluations(graph_run_id=graph_run_id)


async def _list_keys(bucket) -> list[str]:
    """List current KV keys without retaining an inactive watcher consumer."""

    # Lightweight in-memory stores used by domain tests expose only the public
    # keys contract; real NATS KV buckets take the explicit-cleanup path below.
    if not hasattr(bucket, "watchall"):
        return await bucket.keys()
    watcher = await bucket.watchall(ignore_deletes=True, meta_only=True)
    consumer_name: str | None = None
    try:
        info = await watcher._sub.consumer_info()
        consumer_name = info.name
        keys: list[str] = []
        async for entry in watcher:
            if entry is None:
                break
            keys.append(entry.key)
        return keys
    finally:
        await watcher.stop()
        if consumer_name is not None:
            try:
                await bucket._js.delete_consumer(bucket._stream, consumer_name)
            except NotFoundError:
                pass


def _is_request_key(key: str) -> bool:
    if len(key) != 32:
        return False
    try:
        UUID(hex=key)
    except ValueError:
        return False
    return True
