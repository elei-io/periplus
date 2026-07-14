from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from config import get_float, get_int
from config.performance import MATERIALIZATION_ACK_WAIT_SECONDS, RESOURCE_STATE_REPLICAS
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    KeyValueConfig,
    StorageType,
)
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)
from pydantic import BaseModel, ConfigDict, Field
from runtime.catalogue_queue import (
    DEAD_LETTER_STREAM,
    MATERIALIZE_BACKFILL_SUBJECT as SCOPE_BACKFILL_SUBJECT,
    MATERIALIZE_DEAD_LETTER_SUBJECT as DEAD_LETTER_SUBJECT,
    MATERIALIZE_LIVE_SUBJECT as SCOPE_LIVE_SUBJECT,
    WORK_STREAM as SCOPE_STREAM,
    ensure_catalogue_work_stream,
    ensure_dead_letter_stream,
)

SCOPE_LIVE_DURABLE = "atlas-materialization-live-worker"
SCOPE_BACKFILL_DURABLE = "atlas-materialization-backfill-worker"
ATTEMPTS_BUCKET = "atlas_materialization_attempts"


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


class MaterializationDeadLetter(BaseModel):
    model_config = ConfigDict(frozen=True)

    job: MaterializationScopeJob
    stage: Literal["scope"] = "scope"
    error: str
    processing_failure_count: int
    failed_at: datetime
    staging_key: str | None = None


class MaterializationAttemptState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: str
    processing_failure_count: int = Field(ge=0)
    updated_at: datetime


async def ensure_materialization_attempts(jetstream):
    config = KeyValueConfig(
        bucket=ATTEMPTS_BUCKET,
        description="Processing-failure counts for active materialization scopes",
        history=1,
        ttl=get_float("ATLAS_MATERIALIZATION_ATTEMPT_TTL_SECONDS"),
        max_bytes=get_int("ATLAS_MATERIALIZATION_ATTEMPT_MAX_BYTES"),
        storage=StorageType.FILE,
        replicas=RESOURCE_STATE_REPLICAS,
    )
    try:
        bucket = await jetstream.key_value(ATTEMPTS_BUCKET)
    except BucketNotFoundError:
        try:
            bucket = await jetstream.create_key_value(config=config)
        except BadRequestError:
            bucket = await jetstream.key_value(ATTEMPTS_BUCKET)
    status = await bucket.status()
    actual = status.stream_info.config
    if (
        actual.storage != StorageType.FILE
        or actual.max_msgs_per_subject != 1
        or actual.max_age != config.ttl
        or actual.max_bytes != config.max_bytes
        or actual.num_replicas != config.replicas
    ):
        raise RuntimeError(
            f"JetStream KV {ATTEMPTS_BUCKET} has an incompatible contract"
        )
    return bucket


async def record_materialization_processing_failure(bucket, operation_id: str) -> int:
    while True:
        try:
            entry = await bucket.get(operation_id)
        except (KeyNotFoundError, KeyDeletedError):
            state = MaterializationAttemptState(
                operation_id=operation_id,
                processing_failure_count=1,
                updated_at=datetime.now(UTC),
            )
            try:
                await bucket.create(operation_id, state.model_dump_json().encode())
                return 1
            except KeyWrongLastSequenceError:
                continue
        current = MaterializationAttemptState.model_validate_json(entry.value)
        updated = current.model_copy(
            update={
                "processing_failure_count": current.processing_failure_count + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            await bucket.update(
                operation_id,
                updated.model_dump_json().encode(),
                last=entry.revision,
            )
            return updated.processing_failure_count
        except KeyWrongLastSequenceError:
            continue


async def clear_materialization_processing_failures(bucket, operation_id: str) -> None:
    try:
        await bucket.delete(operation_id)
    except (KeyNotFoundError, KeyDeletedError):
        pass


async def ensure_streams(jetstream) -> None:
    await ensure_catalogue_work_stream(jetstream)
    await ensure_dead_letter_stream(jetstream)
    await _ensure_consumer(
        jetstream, SCOPE_STREAM, SCOPE_LIVE_DURABLE, SCOPE_LIVE_SUBJECT
    )
    await _ensure_consumer(
        jetstream,
        SCOPE_STREAM,
        SCOPE_BACKFILL_DURABLE,
        SCOPE_BACKFILL_SUBJECT,
    )


async def _ensure_consumer(
    jetstream, stream: str, durable: str, subject: str
) -> None:
    config = ConsumerConfig(
        durable_name=durable,
        ack_policy=AckPolicy.EXPLICIT,
        filter_subject=subject,
        ack_wait=MATERIALIZATION_ACK_WAIT_SECONDS,
        # Application code distinguishes retryable contention from terminal
        # scope failure and dead-letters only after durable failure coverage.
        max_deliver=-1,
        max_ack_pending=100,
    )
    await jetstream.add_consumer(stream, config)
    info = await jetstream.consumer_info(stream, durable)
    actual = info.config
    if actual.filter_subject != subject or actual.ack_policy != AckPolicy.EXPLICIT:
        raise RuntimeError(f"JetStream consumer {durable} has incompatible configuration")
    if (
        actual.max_deliver != -1
        or actual.ack_wait != MATERIALIZATION_ACK_WAIT_SECONDS
    ):
        raise RuntimeError(f"JetStream consumer {durable} has incompatible delivery limits")
