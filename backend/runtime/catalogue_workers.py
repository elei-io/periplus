"""Ephemeral presence for independently scalable catalogue executors."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import logging
from typing import Literal, Protocol

from config import get_float, get_int
from config.performance import OPERATIONAL_STATE_REPLICAS
from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyDeletedError,
    KeyNotFoundError,
    NoKeysError,
    NotFoundError,
)
from pydantic import BaseModel, ConfigDict, Field


CATALOGUE_WORKERS_BUCKET = "atlas_catalogue_workers"
CatalogueCapability = Literal["ingestion", "materialization"]
_PRESENCE_SUBSYSTEM = "catalogue_worker_presence"


class CatalogueWorkerPresenceMonitor(Protocol):
    def subsystem_ready(self, name: str) -> None: ...

    def subsystem_unavailable(self, name: str, error: str) -> None: ...


class CatalogueWorkerState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_id: str = Field(min_length=1)
    capability: CatalogueCapability
    started_at: datetime
    last_seen_at: datetime
    capacity: int = Field(ge=1)
    active_operation_count: int = Field(ge=0)
    healthy: bool


async def ensure_catalogue_worker_storage(jetstream):
    config = KeyValueConfig(
        bucket=CATALOGUE_WORKERS_BUCKET,
        description="Ephemeral Atlas catalogue-worker presence",
        history=1,
        ttl=get_float("ATLAS_CATALOGUE_WORKER_PRESENCE_TTL_SECONDS"),
        max_bytes=get_int("ATLAS_CATALOGUE_WORKER_MAX_BYTES"),
        storage=StorageType.FILE,
        replicas=OPERATIONAL_STATE_REPLICAS,
    )
    try:
        bucket = await jetstream.key_value(CATALOGUE_WORKERS_BUCKET)
    except BucketNotFoundError:
        try:
            bucket = await jetstream.create_key_value(config=config)
        except BadRequestError:
            bucket = await jetstream.key_value(CATALOGUE_WORKERS_BUCKET)
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
            f"JetStream KV {CATALOGUE_WORKERS_BUCKET} has an incompatible contract"
        )
    return bucket


async def list_catalogue_worker_states(bucket) -> list[CatalogueWorkerState]:
    keys = await _list_keys(bucket)
    states: list[CatalogueWorkerState] = []
    for key in keys:
        try:
            entry = await bucket.get(key)
        except (KeyNotFoundError, KeyDeletedError):
            continue
        states.append(CatalogueWorkerState.model_validate_json(entry.value))
    return states


async def _list_keys(bucket) -> list[str]:
    if not hasattr(bucket, "watchall"):
        try:
            return await bucket.keys()
        except NoKeysError:
            return []
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


async def publish_catalogue_worker_state(bucket, state: CatalogueWorkerState) -> None:
    await bucket.put(state.worker_id.replace(":", "-"), state.model_dump_json().encode())


async def catalogue_worker_presence(
    bucket,
    *,
    worker_id: str,
    capability: CatalogueCapability,
    started_at: datetime,
    active_operation_count: Callable[[], int],
    healthy: Callable[[], bool],
    stop: asyncio.Event,
    monitor: CatalogueWorkerPresenceMonitor | None = None,
) -> None:
    if monitor is not None:
        monitor.subsystem_unavailable(
            _PRESENCE_SUBSYSTEM,
            "catalogue worker presence has not been published",
        )
    while not stop.is_set():
        try:
            await publish_catalogue_worker_state(
                bucket,
                CatalogueWorkerState(
                    worker_id=worker_id,
                    capability=capability,
                    started_at=started_at,
                    last_seen_at=datetime.now(UTC),
                    capacity=1,
                    active_operation_count=active_operation_count(),
                    healthy=healthy(),
                ),
            )
        except Exception as exc:
            if monitor is not None:
                monitor.subsystem_unavailable(
                    _PRESENCE_SUBSYSTEM,
                    str(exc) or type(exc).__name__,
                )
            logging.warning("catalogue worker presence publication failed", exc_info=True)
        else:
            if monitor is not None:
                monitor.subsystem_ready(_PRESENCE_SUBSYSTEM)
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass
