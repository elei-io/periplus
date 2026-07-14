from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from config import get_float
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
)
from pydantic import BaseModel, ConfigDict
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
    delivery_count: int
    failed_at: datetime
    staging_key: str | None = None


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
        ack_wait=get_float("ATLAS_MATERIALIZATION_ACK_WAIT_SECONDS"),
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
        or actual.ack_wait != get_float("ATLAS_MATERIALIZATION_ACK_WAIT_SECONDS")
    ):
        raise RuntimeError(f"JetStream consumer {durable} has incompatible delivery limits")
