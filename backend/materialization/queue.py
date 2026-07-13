from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from config import get_float, get_int
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import NotFoundError
from pydantic import BaseModel, ConfigDict

SCOPE_STREAM = "ATLAS_MATERIALIZATION_SCOPES"
SCOPE_SUBJECT = "atlas.materialization.scope.*"
SCOPE_LIVE_SUBJECT = "atlas.materialization.scope.live"
SCOPE_BACKFILL_SUBJECT = "atlas.materialization.scope.backfill"
SCOPE_LIVE_DURABLE = "atlas-materialization-live-worker"
SCOPE_BACKFILL_DURABLE = "atlas-materialization-backfill-worker"
COMMIT_STREAM = "ATLAS_MATERIALIZATION_COMMITS"
COMMIT_SUBJECT = "atlas.materialization.commit"
COMMIT_DURABLE = "atlas-repository-materialization-writer"
DEAD_LETTER_STREAM = "ATLAS_MATERIALIZATION_DEAD_LETTER"
DEAD_LETTER_SUBJECT = "atlas.materialization.dead_letter"


class MaterializationScopeJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    materialization_id: UUID
    definition_revision_id: UUID
    target_table: str
    scope_kind: Literal["document", "crawl"]
    scope_column: str
    scope_id: str
    operation_id: str
    source: Literal["live", "backfill"]
    enqueued_at: datetime


class MaterializationCommitJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["commit"] = "commit"
    scope: MaterializationScopeJob
    staging_key: str
    staging_sha256: str
    row_count: int
    output_bytes: int
    file_bytes: int
    started_at: datetime
    completed_at: datetime


class MaterializationFailureJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["failure"] = "failure"
    scope: MaterializationScopeJob
    reason: Literal["execution", "stale"] = "execution"
    error: str
    started_at: datetime
    completed_at: datetime


class CrawlMaterializationFanoutPlanJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["fanout_plan"] = "fanout_plan"
    crawl_id: UUID
    scopes: list[MaterializationScopeJob]
    planned_at: datetime


class MaterializationDeadLetter(BaseModel):
    model_config = ConfigDict(frozen=True)

    job: MaterializationScopeJob
    stage: Literal["compute", "commit"]
    error: str
    delivery_count: int
    failed_at: datetime
    staging_key: str | None = None


async def ensure_streams(jetstream) -> None:
    replicas = get_int("ATLAS_MATERIALIZATION_STREAM_REPLICAS")
    max_deliver = get_int("ATLAS_MATERIALIZATION_MAX_DELIVER")
    await _ensure_stream(
        jetstream,
        name=SCOPE_STREAM,
        subjects=[SCOPE_SUBJECT],
        retention=RetentionPolicy.WORK_QUEUE,
        replicas=replicas,
    )
    await _ensure_stream(
        jetstream,
        name=COMMIT_STREAM,
        subjects=[COMMIT_SUBJECT],
        retention=RetentionPolicy.WORK_QUEUE,
        replicas=replicas,
    )
    await _ensure_stream(
        jetstream,
        name=DEAD_LETTER_STREAM,
        subjects=[DEAD_LETTER_SUBJECT],
        retention=RetentionPolicy.LIMITS,
        replicas=replicas,
        max_bytes=get_int("ATLAS_MATERIALIZATION_DEAD_LETTER_MAX_BYTES"),
        max_age=get_int("ATLAS_MATERIALIZATION_DEAD_LETTER_TTL_SECONDS"),
    )
    await _ensure_consumer(
        jetstream, SCOPE_STREAM, SCOPE_LIVE_DURABLE, SCOPE_LIVE_SUBJECT, max_deliver
    )
    await _ensure_consumer(
        jetstream,
        SCOPE_STREAM,
        SCOPE_BACKFILL_DURABLE,
        SCOPE_BACKFILL_SUBJECT,
        max_deliver,
    )
    await _ensure_consumer(
        jetstream, COMMIT_STREAM, COMMIT_DURABLE, COMMIT_SUBJECT, -1
    )


async def _ensure_stream(
    jetstream,
    *,
    name: str,
    subjects: list[str],
    retention: RetentionPolicy,
    replicas: int,
    max_bytes: int = -1,
    max_age: float = 0,
) -> None:
    config = StreamConfig(
        name=name,
        subjects=subjects,
        retention=retention,
        storage=StorageType.FILE,
        num_replicas=replicas,
        max_bytes=max_bytes,
        max_age=max_age,
    )
    try:
        info = await jetstream.stream_info(name)
    except NotFoundError:
        await jetstream.add_stream(config)
        return
    actual = info.config
    if set(actual.subjects) != set(subjects):
        raise RuntimeError(f"JetStream {name} has unexpected subjects")
    if actual.retention != retention or actual.storage != StorageType.FILE:
        raise RuntimeError(f"JetStream {name} has incompatible retention or storage")
    if actual.num_replicas != replicas:
        raise RuntimeError(f"JetStream {name} has {actual.num_replicas} replicas; expected {replicas}")
    if actual.max_bytes != max_bytes or actual.max_age != max_age:
        raise RuntimeError(f"JetStream {name} has incompatible retention limits")


async def _ensure_consumer(
    jetstream, stream: str, durable: str, subject: str, max_deliver: int
) -> None:
    config = ConsumerConfig(
        durable_name=durable,
        ack_policy=AckPolicy.EXPLICIT,
        filter_subject=subject,
        ack_wait=get_float("ATLAS_MATERIALIZATION_ACK_WAIT_SECONDS"),
        max_deliver=max_deliver,
        max_ack_pending=100,
    )
    await jetstream.add_consumer(stream, config)
    info = await jetstream.consumer_info(stream, durable)
    actual = info.config
    if actual.filter_subject != subject or actual.ack_policy != AckPolicy.EXPLICIT:
        raise RuntimeError(f"JetStream consumer {durable} has incompatible configuration")
    if (
        actual.max_deliver != max_deliver
        or actual.ack_wait != get_float("ATLAS_MATERIALIZATION_ACK_WAIT_SECONDS")
    ):
        raise RuntimeError(f"JetStream consumer {durable} has incompatible delivery limits")
