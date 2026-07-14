"""JetStream work and KV-backed current crawl-graph execution state."""

from __future__ import annotations

import hashlib
import asyncio
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import nats
from config import get_float, get_int, get_str
from nats.js.api import AckPolicy, ConsumerConfig, KeyValueConfig, RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import BadRequestError, BucketNotFoundError, KeyDeletedError, KeyNotFoundError, KeyWrongLastSequenceError, NotFoundError
from pydantic import BaseModel, ConfigDict, Field
from control.crawl_graphs.schemas import EdgeDedupeMode, FrozenGraphEdge, FrozenGraphNode, FrozenGraphSnapshot
from control.crawl_policies.schemas import CrawlPolicyConfig, CrawlPolicySnapshot
from control.url_matching import normalize_url
from runtime.navigation_contract import NavigationPackage

GRAPH_STREAM = "ATLAS_GRAPH_WORK"
CRAWL_HTTP_SUBJECT = "atlas.graph.crawl.http"
CRAWL_BROWSER_SUBJECT = "atlas.graph.crawl.browser"
CRAWL_FIRECRAWL_SUBJECT = "atlas.graph.crawl.provider.firecrawl"
EDGE_SUBJECT = "atlas.graph.edge"
READINESS_SUBJECT = "atlas.graph.readiness"
CRAWL_HTTP_CONSUMER = "atlas-graph-crawl-http-workers"
CRAWL_BROWSER_CONSUMER = "atlas-graph-crawl-browser-workers"
CRAWL_FIRECRAWL_CONSUMER = "atlas-graph-crawl-firecrawl-workers"
EDGE_CONSUMER = "atlas-graph-edge-workers"
READINESS_CONSUMER = "atlas-graph-readiness-workers"
RUNS_BUCKET = "atlas_graph_runs"
REQUESTS_BUCKET = "atlas_crawl_requests"
WORKERS_BUCKET = "atlas_graph_workers"
PROGRESS_BUCKET = "atlas_graph_progress"
POLICY_TRIAL_BUDGET_BUCKET = "atlas_policy_trial_budget"
POLICY_TRIAL_BUDGET_KEY = "active"

GraphRunStatus = Literal["queued", "running", "completed", "completed_with_errors", "failed", "cancelled"]
CrawlRequestStatus = Literal["queued", "crawling", "awaiting_ingestion", "awaiting_navigation", "evaluating_edges", "completed", "failed", "cancelled"]
FailureStage = Literal["admission", "acquisition", "enrichment", "edge", "lifecycle"]
TriggerKind = Literal["manual"]
CrawlTransport = Literal["http", "browser", "firecrawl"]
CrawlPurpose = Literal["use", "sample"]

CRAWL_SUBJECTS: dict[CrawlTransport, str] = {
    "http": CRAWL_HTTP_SUBJECT,
    "browser": CRAWL_BROWSER_SUBJECT,
    "firecrawl": CRAWL_FIRECRAWL_SUBJECT,
}
CRAWL_CONSUMERS: dict[CrawlTransport, str] = {
    "http": CRAWL_HTTP_CONSUMER,
    "browser": CRAWL_BROWSER_CONSUMER,
    "firecrawl": CRAWL_FIRECRAWL_CONSUMER,
}


class PolicyTrialMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)
    trial_id: UUID
    sampler_version: int = Field(ge=1)
    sample_share: float = Field(ge=0, le=1)
    candidate_strategy: Literal["next_more_expensive_template"]
    candidate_template: str
    template_registry_version: int = Field(ge=1)


class PendingAdmission(BaseModel):
    """Frozen request creation/publication intent stored with the run reservation."""

    model_config = ConfigDict(frozen=True)
    request_id: UUID
    identity: str
    node_id: UUID
    url: str
    transport: CrawlTransport
    effective_policy_snapshot_json: dict
    sample_request_id: UUID | None = None
    sample_transport: CrawlTransport | None = None
    sample_policy_snapshot_json: dict | None = None
    trial: PolicyTrialMetadata | None = None
    source_crawl_id: UUID | None = None
    source_edge_id: UUID | None = None
    parent_request_id: UUID | None = None
    created_at: datetime


class GraphRun(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    graph_id: UUID
    trigger_kind: TriggerKind
    status: GraphRunStatus = "queued"
    snapshot: FrozenGraphSnapshot
    trigger_urls: tuple[str, ...]
    seen_request_identities: tuple[str, ...] = ()
    pending_admissions: tuple[PendingAdmission, ...] = ()
    request_count: int = 0
    pending_request_count: int = 0
    failed_request_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    created_at: datetime
    started_at: datetime | None = None
    last_progress_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    error: str | None = None


class CrawlRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    graph_run_id: UUID
    node_id: UUID
    url: str
    transport: CrawlTransport
    purpose: CrawlPurpose = "use"
    trial: PolicyTrialMetadata | None = None
    document_id: str | None = None
    effective_policy_snapshot_json: dict
    source_crawl_id: UUID | None = None
    source_edge_id: UUID | None = None
    parent_request_id: UUID | None = None
    status: CrawlRequestStatus = "queued"
    claim_token: UUID | None = None
    claim_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    failure_stage: FailureStage | None = None


class PolicyTrialBudget(BaseModel):
    model_config = ConfigDict(frozen=True)
    active_sample_request_ids: tuple[UUID, ...] = ()


class CrawlWork(BaseModel):
    model_config = ConfigDict(frozen=True)
    crawl_request_id: UUID
    transport: CrawlTransport


class EdgeWork(BaseModel):
    model_config = ConfigDict(frozen=True)
    graph_run_id: UUID
    crawl_request_id: UUID
    crawl_id: UUID
    edge_id: UUID
    navigation: NavigationPackage


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
    error: str | None = None


class ReadinessWork(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_id: UUID
    crawl_id: UUID
    graph_run_id: UUID
    crawl_request_id: UUID
    navigation: NavigationPackage
    occurred_at: datetime


class WorkerState(BaseModel):
    model_config = ConfigDict(frozen=True)
    worker_id: str
    transport: CrawlTransport
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
    subjects = [*CRAWL_SUBJECTS.values(), EDGE_SUBJECT, READINESS_SUBJECT]
    stream = StreamConfig(name=GRAPH_STREAM, subjects=subjects, retention=RetentionPolicy.WORK_QUEUE, storage=StorageType.FILE, num_replicas=replicas)
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
        ):
            raise RuntimeError(
                f"JetStream {GRAPH_STREAM} has the superseded graph-work contract; "
                "reset disposable NATS state before starting Atlas"
            )
    ack_wait = get_float("ATLAS_GRAPH_ACK_WAIT_SECONDS")
    consumers = (
        (
            CRAWL_HTTP_CONSUMER,
            CRAWL_HTTP_SUBJECT,
            get_int("ATLAS_CRAWL_HTTP_MAX_ACK_PENDING"),
        ),
        (
            CRAWL_BROWSER_CONSUMER,
            CRAWL_BROWSER_SUBJECT,
            get_int("ATLAS_CRAWL_BROWSER_MAX_ACK_PENDING"),
        ),
        (
            CRAWL_FIRECRAWL_CONSUMER,
            CRAWL_FIRECRAWL_SUBJECT,
            get_int("ATLAS_CRAWL_PROVIDER_MAX_ACK_PENDING"),
        ),
        (EDGE_CONSUMER, EDGE_SUBJECT, get_int("ATLAS_INGESTION_WORKER_CONCURRENCY")),
        (
            READINESS_CONSUMER,
            READINESS_SUBJECT,
            get_int("ATLAS_INGESTION_WORKER_CONCURRENCY"),
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
        await jetstream.add_consumer(GRAPH_STREAM, config=expected)
        actual = (await jetstream.consumer_info(GRAPH_STREAM, durable)).config
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
            ttl=get_float("ATLAS_GRAPH_PROGRESS_TTL_SECONDS"),
            max_bytes=get_int("ATLAS_GRAPH_STATE_MAX_BYTES"),
            storage=StorageType.FILE,
            replicas=get_int("ATLAS_GRAPH_STREAM_REPLICAS"),
        ),
    )


async def ensure_policy_trial_budget_storage(jetstream):
    return await _bucket(
        jetstream,
        KeyValueConfig(
            bucket=POLICY_TRIAL_BUDGET_BUCKET,
            description="Active Atlas crawl-policy sample reservations",
            history=1,
            max_bytes=get_int("ATLAS_GRAPH_STATE_MAX_BYTES"),
            storage=StorageType.FILE,
            replicas=get_int("ATLAS_GRAPH_STREAM_REPLICAS"),
        ),
    )


async def reserve_policy_trial_slot(bucket, request_id: UUID, maximum: int) -> bool:
    if maximum < 1:
        return False
    while True:
        try:
            entry = await bucket.get(POLICY_TRIAL_BUDGET_KEY)
        except (KeyNotFoundError, KeyDeletedError):
            initial = PolicyTrialBudget(active_sample_request_ids=(request_id,))
            try:
                await bucket.create(
                    POLICY_TRIAL_BUDGET_KEY, initial.model_dump_json().encode()
                )
                return True
            except KeyWrongLastSequenceError:
                continue
        current = PolicyTrialBudget.model_validate_json(entry.value)
        if request_id in current.active_sample_request_ids:
            return True
        if len(current.active_sample_request_ids) >= maximum:
            return False
        updated = current.model_copy(
            update={
                "active_sample_request_ids": (
                    *current.active_sample_request_ids,
                    request_id,
                )
            }
        )
        try:
            await bucket.update(
                POLICY_TRIAL_BUDGET_KEY,
                updated.model_dump_json().encode(),
                last=entry.revision,
            )
            return True
        except KeyWrongLastSequenceError:
            continue


async def release_policy_trial_slot(bucket, request_id: UUID) -> None:
    while True:
        try:
            entry = await bucket.get(POLICY_TRIAL_BUDGET_KEY)
        except (KeyNotFoundError, KeyDeletedError):
            return
        current = PolicyTrialBudget.model_validate_json(entry.value)
        if request_id not in current.active_sample_request_ids:
            return
        updated = current.model_copy(
            update={
                "active_sample_request_ids": tuple(
                    value
                    for value in current.active_sample_request_ids
                    if value != request_id
                )
            }
        )
        try:
            await bucket.update(
                POLICY_TRIAL_BUDGET_KEY,
                updated.model_dump_json().encode(),
                last=entry.revision,
            )
            return
        except KeyWrongLastSequenceError:
            continue


async def reconcile_policy_trial_budget(bucket, runs, requests) -> PolicyTrialBudget:
    """Repair reservations from authoritative current run and request state."""

    active_runs, crawl_requests = await asyncio.gather(
        list_graph_runs(runs), list_crawl_requests(requests)
    )
    valid_pending = {
        pending.sample_request_id
        for run in active_runs
        if run.status in {"queued", "running"}
        for pending in run.pending_admissions
        if pending.trial is not None and pending.sample_request_id is not None
    }
    active_requests = {
        request.id
        for request in crawl_requests
        if request.purpose == "sample"
        and request.status not in {"completed", "failed", "cancelled"}
    }
    while True:
        try:
            entry = await bucket.get(POLICY_TRIAL_BUDGET_KEY)
        except (KeyNotFoundError, KeyDeletedError):
            repaired = PolicyTrialBudget(
                active_sample_request_ids=tuple(sorted(active_requests, key=str))
            )
            try:
                await bucket.create(
                    POLICY_TRIAL_BUDGET_KEY, repaired.model_dump_json().encode()
                )
                return repaired
            except KeyWrongLastSequenceError:
                continue
        current = PolicyTrialBudget.model_validate_json(entry.value)
        valid = active_requests | (
            set(current.active_sample_request_ids) & valid_pending
        )
        repaired = PolicyTrialBudget(
            active_sample_request_ids=tuple(sorted(valid, key=str))
        )
        try:
            await bucket.update(
                POLICY_TRIAL_BUDGET_KEY,
                repaired.model_dump_json().encode(),
                last=entry.revision,
            )
            return repaired
        except KeyWrongLastSequenceError:
            continue


async def settle_sample_request(
    jetstream,
    requests,
    request_id: UUID,
    *,
    status: Literal["completed", "failed", "cancelled"],
    error: str | None = None,
    failure_stage: FailureStage | None = None,
    expected_claim_token: UUID | None = None,
) -> CrawlRequest:
    def settle(current: CrawlRequest) -> CrawlRequest:
        if current.purpose != "sample":
            raise ValueError("only sample crawl requests use sample settlement")
        if current.status in {"completed", "failed", "cancelled"}:
            return current
        if expected_claim_token is not None and current.claim_token != expected_claim_token:
            return current
        return current.model_copy(
            update={
                "status": status,
                "error": error,
                "failure_stage": failure_stage if status == "failed" else None,
                "claim_token": None,
                "claim_expires_at": None,
                "updated_at": datetime.now(UTC),
            }
        )

    request = await update_crawl_request(requests, request_id, settle)
    if request.status in {"completed", "failed", "cancelled"}:
        budget = await ensure_policy_trial_budget_storage(jetstream)
        await release_policy_trial_slot(budget, request_id)
    return request


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
    keys = await _list_keys(bucket)
    return [value for key in keys if (value := await _get(bucket, key, GraphRun)) is not None]


async def list_worker_states(bucket) -> list[WorkerState]:
    keys = await _list_keys(bucket)
    return [
        value
        for key in keys
        if (value := await _get(bucket, key, WorkerState)) is not None
    ]


async def list_crawl_requests(bucket, *, graph_run_id: UUID | None = None) -> list[CrawlRequest]:
    keys = await _list_keys(bucket)
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
    keys = await _list_keys(bucket)
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


def crawl_transport_from_policy(
    policy_snapshot_json: dict,
) -> CrawlTransport:
    snapshot = CrawlPolicySnapshot.model_validate(policy_snapshot_json)
    return CrawlPolicyConfig.model_validate(snapshot.config).profile


async def publish_crawl(jetstream, request: CrawlRequest) -> None:
    subject = CRAWL_SUBJECTS[request.transport]
    work = CrawlWork(crawl_request_id=request.id, transport=request.transport)
    await jetstream.publish(
        subject,
        work.model_dump_json().encode(),
        stream=GRAPH_STREAM,
        headers={"Nats-Msg-Id": request.id.hex},
    )


async def publish_edge(jetstream, work: EdgeWork) -> None:
    identity = edge_evaluation_identity(work.graph_run_id, work.crawl_request_id, work.crawl_id, work.edge_id)
    await jetstream.publish(EDGE_SUBJECT, work.model_dump_json().encode(), stream=GRAPH_STREAM, headers={"Nats-Msg-Id": identity})
