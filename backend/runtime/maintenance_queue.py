"""Durable delivery and current state for off-path catalogue maintenance."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from config import get_float, get_int
from nats.js.api import AckPolicy, ConsumerConfig, KeyValueConfig, RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import BadRequestError, BucketNotFoundError, NotFoundError
from pydantic import BaseModel, ConfigDict

MAINTENANCE_STREAM = "ATLAS_MAINTENANCE_WORK"
MAINTENANCE_SUBJECT_PREFIX = "atlas.maintenance"
MAINTENANCE_SUBJECT = f"{MAINTENANCE_SUBJECT_PREFIX}.*"
MAINTENANCE_CONSUMER = "atlas-maintenance-workers"
MAINTENANCE_WORKERS_BUCKET = "atlas_maintenance_workers"
MAINTENANCE_OPERATIONS_BUCKET = "atlas_maintenance_operations"
MAINTENANCE_LEASE_BUCKET = "atlas_catalog_maintenance"

MaintenanceKind = Literal["flush", "compact", "cleanup", "retention", "repair"]


class MaintenanceJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    operation_id: str
    kind: MaintenanceKind
    requested_at: datetime
    table_name: str | None = None


class MaintenanceOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    operation_id: str
    kind: MaintenanceKind
    status: Literal["running", "completed", "failed"]
    worker_id: str
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class MaintenanceLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    owner: str
    operation_id: str
    kind: MaintenanceKind
    acquired_at: datetime
    heartbeat_at: datetime


class MaintenanceWorkerState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    worker_id: str
    started_at: datetime
    last_seen_at: datetime
    active_operation_id: str | None = None
    stopping: bool = False


def maintenance_operation_id(kind: MaintenanceKind, epoch_bucket: int, table_name: str | None = None) -> str:
    value = f"maintenance\0{kind}\0{epoch_bucket}\0{table_name or ''}"
    return hashlib.sha256(value.encode()).hexdigest()


async def _bucket(jetstream, config: KeyValueConfig):
    try:
        return await jetstream.key_value(config.bucket)
    except BucketNotFoundError:
        try:
            return await jetstream.create_key_value(config=config)
        except BadRequestError:
            return await jetstream.key_value(config.bucket)


async def ensure_maintenance_storage(jetstream):
    replicas = get_int("ATLAS_MAINTENANCE_STREAM_REPLICAS")
    try:
        await jetstream.stream_info(MAINTENANCE_STREAM)
    except NotFoundError:
        await jetstream.add_stream(config=StreamConfig(
            name=MAINTENANCE_STREAM,
            subjects=[MAINTENANCE_SUBJECT],
            retention=RetentionPolicy.WORK_QUEUE,
            storage=StorageType.FILE,
            num_replicas=replicas,
        ))
    await jetstream.add_consumer(MAINTENANCE_STREAM, config=ConsumerConfig(
        durable_name=MAINTENANCE_CONSUMER,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=get_float("ATLAS_MAINTENANCE_ACK_WAIT_SECONDS"),
        filter_subject=MAINTENANCE_SUBJECT,
        max_ack_pending=get_int("ATLAS_MAINTENANCE_WORKER_CONCURRENCY"),
        max_deliver=get_int("ATLAS_MAINTENANCE_MAX_DELIVER"),
    ))
    workers = await _bucket(jetstream, KeyValueConfig(
        bucket=MAINTENANCE_WORKERS_BUCKET,
        description="Ephemeral Atlas maintenance-worker presence",
        history=1,
        ttl=get_float("ATLAS_MAINTENANCE_WORKER_PRESENCE_TTL_SECONDS"),
        storage=StorageType.FILE,
        replicas=replicas,
    ))
    operations = await _bucket(jetstream, KeyValueConfig(
        bucket=MAINTENANCE_OPERATIONS_BUCKET,
        description="Current idempotent Atlas maintenance operations",
        history=1,
        storage=StorageType.FILE,
        replicas=replicas,
    ))
    lease = await _bucket(jetstream, KeyValueConfig(
        bucket=MAINTENANCE_LEASE_BUCKET,
        description="Global DuckLake maintenance lease",
        history=1,
        ttl=get_float("ATLAS_MAINTENANCE_LEASE_SECONDS"),
        storage=StorageType.FILE,
        replicas=replicas,
    ))
    return workers, operations, lease
