"""Bounded NATS state for active and recent interactive catalogue queries."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from config import get_float, get_int
from config.performance import OPERATIONAL_STATE_REPLICAS
from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)
from pydantic import BaseModel, ConfigDict, Field

from repository.catalogue.query import CatalogueStatementKind


CATALOGUE_QUERIES_BUCKET = "atlas_catalogue_queries"
CatalogueQueryStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
]


class CatalogueQueryState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    statement_kind: CatalogueStatementKind
    status: CatalogueQueryStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    row_count: int = Field(default=0, ge=0)
    result_bytes: int = Field(default=0, ge=0)
    error: str | None = None


async def ensure_catalogue_query_storage(jetstream):
    config = KeyValueConfig(
        bucket=CATALOGUE_QUERIES_BUCKET,
        description="Active and recent Atlas interactive catalogue queries",
        history=1,
        ttl=get_float("ATLAS_QUACK_QUERY_STATE_TTL_SECONDS"),
        max_bytes=get_int("ATLAS_QUACK_QUERY_STATE_MAX_BYTES"),
        storage=StorageType.FILE,
        replicas=OPERATIONAL_STATE_REPLICAS,
    )
    try:
        bucket = await jetstream.key_value(CATALOGUE_QUERIES_BUCKET)
    except BucketNotFoundError:
        try:
            bucket = await jetstream.create_key_value(config=config)
        except BadRequestError:
            bucket = await jetstream.key_value(CATALOGUE_QUERIES_BUCKET)
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
            f"JetStream KV {CATALOGUE_QUERIES_BUCKET} has an incompatible contract"
        )
    return bucket


async def create_catalogue_query(bucket, state: CatalogueQueryState) -> None:
    await bucket.create(state.id.hex, state.model_dump_json().encode())


async def get_catalogue_query(bucket, query_id: UUID) -> CatalogueQueryState | None:
    try:
        entry = await bucket.get(query_id.hex)
    except (KeyNotFoundError, KeyDeletedError):
        return None
    return CatalogueQueryState.model_validate_json(entry.value)


async def update_catalogue_query(
    bucket,
    query_id: UUID,
    mutate: Callable[[CatalogueQueryState], CatalogueQueryState],
) -> CatalogueQueryState | None:
    while True:
        try:
            entry = await bucket.get(query_id.hex)
        except (KeyNotFoundError, KeyDeletedError):
            return None
        current = CatalogueQueryState.model_validate_json(entry.value)
        updated = mutate(current)
        try:
            await bucket.update(
                query_id.hex,
                updated.model_dump_json().encode(),
                last=entry.revision,
            )
            return updated
        except KeyWrongLastSequenceError:
            continue


def request_catalogue_query_cancellation(
    state: CatalogueQueryState,
) -> CatalogueQueryState:
    if state.status in {"succeeded", "failed", "cancelled"}:
        return state
    return state.model_copy(
        update={"cancel_requested_at": state.cancel_requested_at or datetime.now(UTC)}
    )
