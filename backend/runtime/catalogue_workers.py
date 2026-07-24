"""Ephemeral presence for independently scalable catalogue executors."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
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

from .nats_client import connect_nats


CATALOGUE_WORKERS_BUCKET = "atlas_catalogue_workers"
CatalogueCapability = Literal["ingestion", "materialization"]
CatalogueLaneStatus = Literal["starting", "available", "active", "unavailable"]
_PRESENCE_SUBSYSTEM = "catalogue_worker_presence"


class CatalogueWorkerPresenceMonitor(Protocol):
    def subsystem_ready(self, name: str) -> None: ...

    def subsystem_unavailable(self, name: str, error: str) -> None: ...


@dataclass(slots=True)
class CatalogueLaneReporter:
    lane_index: int
    active_operation_count: int = 0
    _health: Callable[[], tuple[bool, str]] | None = field(
        default=None, repr=False
    )

    def attach(self, health: Callable[[], tuple[bool, str]]) -> None:
        self._health = health

    def snapshot(self) -> CatalogueLaneState:
        if self._health is None:
            return CatalogueLaneState(
                lane_index=self.lane_index,
                status="starting",
                active=False,
                detail="lane has not initialized",
            )
        ready, detail = self._health()
        if not ready:
            return CatalogueLaneState(
                lane_index=self.lane_index,
                status="unavailable",
                active=self.active_operation_count > 0,
                detail=detail,
            )
        return CatalogueLaneState(
            lane_index=self.lane_index,
            status="active" if self.active_operation_count > 0 else "available",
            active=self.active_operation_count > 0,
        )


class CatalogueLaneState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lane_index: int = Field(ge=0)
    status: CatalogueLaneStatus
    active: bool
    detail: str | None = None

    @property
    def usable(self) -> bool:
        return self.status in {"available", "active"}


class CatalogueWorkerState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_id: str = Field(min_length=1)
    capability: CatalogueCapability
    started_at: datetime
    last_seen_at: datetime
    lanes: tuple[CatalogueLaneState, ...] = Field(min_length=1)
    process_ready: bool
    process_detail: str | None = None

    @property
    def configured_capacity(self) -> int:
        return len(self.lanes)

    @property
    def usable_capacity(self) -> int:
        return sum(lane.usable for lane in self.lanes)

    @property
    def active_operation_count(self) -> int:
        return sum(lane.active for lane in self.lanes)

    @property
    def healthy(self) -> bool:
        return self.process_ready and self.usable_capacity > 0


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
    lanes: Callable[[], tuple[CatalogueLaneState, ...]],
    process_health: Callable[[], tuple[bool, str]],
    stop: asyncio.Event,
    monitor: CatalogueWorkerPresenceMonitor | None = None,
) -> None:
    if monitor is not None:
        monitor.subsystem_unavailable(
            _PRESENCE_SUBSYSTEM,
            "catalogue worker presence has not been published",
        )
    while not stop.is_set():
        process_ready, process_detail = process_health()
        try:
            await publish_catalogue_worker_state(
                bucket,
                CatalogueWorkerState(
                    worker_id=worker_id,
                    capability=capability,
                    started_at=started_at,
                    last_seen_at=datetime.now(UTC),
                    lanes=lanes(),
                    process_ready=process_ready,
                    process_detail=None if process_ready else process_detail,
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


async def run_catalogue_process_presence(
    *,
    worker_id: str,
    capability: CatalogueCapability,
    started_at: datetime,
    lane_reporters: tuple[CatalogueLaneReporter, ...],
    process_health: Callable[[], tuple[bool, str]],
    stop: asyncio.Event,
    monitor: CatalogueWorkerPresenceMonitor,
) -> None:
    client = await connect_nats()
    try:
        bucket = await ensure_catalogue_worker_storage(client.jetstream())
        await catalogue_worker_presence(
            bucket,
            worker_id=worker_id,
            capability=capability,
            started_at=started_at,
            lanes=lambda: tuple(lane.snapshot() for lane in lane_reporters),
            process_health=process_health,
            stop=stop,
            monitor=monitor,
        )
    finally:
        await client.drain()


async def monitor_catalogue_lanes(
    *,
    lane_reporters: tuple[CatalogueLaneReporter, ...],
    stop: asyncio.Event,
    monitor: CatalogueWorkerPresenceMonitor,
) -> None:
    subsystem = "catalogue_lane_capacity"
    while not stop.is_set():
        states = tuple(lane.snapshot() for lane in lane_reporters)
        usable = sum(lane.usable for lane in states)
        if usable > 0:
            monitor.subsystem_ready(subsystem)
        else:
            details = "; ".join(
                f"lane {lane.lane_index}: {lane.detail or lane.status}"
                for lane in states
            )
            monitor.subsystem_unavailable(subsystem, details)
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass
