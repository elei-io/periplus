"""Expiring NATS leases for retry-stable catalogue operations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from config import get_int
from config.performance import (
    CATALOGUE_OPERATION_ACQUIRE_TIMEOUT_SECONDS,
    CATALOGUE_OPERATION_HEARTBEAT_SECONDS,
    CATALOGUE_OPERATION_LEASE_REPLICAS,
    CATALOGUE_OPERATION_LEASE_SECONDS,
)
from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)
from pydantic import BaseModel, ConfigDict

OPERATION_LEASE_BUCKET = "atlas_catalog_operations"


class OperationLeaseUnavailable(RuntimeError):
    """Another worker still owns at least one requested operation."""


class OperationLeaseLost(RuntimeError):
    """A worker could not renew an operation lease it previously owned."""


class OperationLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner: str
    phase: str
    operation_id: str
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime


class OperationLeaseGuard:
    """Expose lease loss to long-running leader subsystems."""

    def __init__(self, lost: asyncio.Event) -> None:
        self._lost = lost

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    async def wait_lost(self) -> None:
        await self._lost.wait()


def operation_lease_key(phase: str, operation_id: str) -> str:
    """Keep arbitrary operation identities inside JetStream KV key syntax."""

    digest = sha256(f"{phase}\0{operation_id}".encode()).hexdigest()
    return f"{phase}-{digest}"


async def ensure_operation_lease_storage(jetstream):
    """Attach or create the self-expiring catalogue operation lease bucket."""

    config = KeyValueConfig(
        bucket=OPERATION_LEASE_BUCKET,
        description="Expiring Atlas catalogue operation leases",
        history=1,
        ttl=CATALOGUE_OPERATION_LEASE_SECONDS,
        max_bytes=get_int("ATLAS_OPERATION_LEASE_MAX_BYTES"),
        storage=StorageType.FILE,
        replicas=CATALOGUE_OPERATION_LEASE_REPLICAS,
    )
    try:
        bucket = await jetstream.key_value(OPERATION_LEASE_BUCKET)
    except BucketNotFoundError:
        try:
            bucket = await jetstream.create_key_value(config=config)
        except BadRequestError:
            bucket = await jetstream.key_value(OPERATION_LEASE_BUCKET)
    await _validate_bucket(bucket)
    return bucket


async def _validate_bucket(bucket) -> None:
    status = await bucket.status()
    config = status.stream_info.config
    expected_ttl = CATALOGUE_OPERATION_LEASE_SECONDS
    expected_max_bytes = get_int("ATLAS_OPERATION_LEASE_MAX_BYTES")
    expected_replicas = CATALOGUE_OPERATION_LEASE_REPLICAS
    mismatches: list[str] = []
    if config.storage != StorageType.FILE:
        mismatches.append("file storage")
    if config.max_msgs_per_subject != 1:
        mismatches.append("history=1")
    if config.max_age != expected_ttl:
        mismatches.append(f"ttl={expected_ttl:g}s")
    if config.max_bytes != expected_max_bytes:
        mismatches.append(f"max_bytes={expected_max_bytes}")
    if config.num_replicas != expected_replicas:
        mismatches.append(f"replicas={expected_replicas}")
    if mismatches:
        raise RuntimeError(
            f"JetStream KV {OPERATION_LEASE_BUCKET} must use " + ", ".join(mismatches)
        )


async def _try_acquire(bucket, *, phase: str, operation_id: str, owner: str) -> bool:
    key = operation_lease_key(phase, operation_id)
    now = datetime.now(UTC)
    lease = OperationLease(
        owner=owner,
        phase=phase,
        operation_id=operation_id,
        acquired_at=now,
        heartbeat_at=now,
        expires_at=now
        + timedelta(seconds=CATALOGUE_OPERATION_LEASE_SECONDS),
    )
    try:
        entry = await bucket.get(key)
    except (KeyNotFoundError, KeyDeletedError):
        try:
            await bucket.create(key, lease.model_dump_json().encode())
            return True
        except KeyWrongLastSequenceError:
            return False

    current = OperationLease.model_validate_json(entry.value)
    if current.owner != owner and current.expires_at > now:
        return False
    renewed = lease.model_copy(
        update={"acquired_at": current.acquired_at if current.owner == owner else now}
    )
    try:
        await bucket.update(key, renewed.model_dump_json().encode(), last=entry.revision)
        return True
    except KeyWrongLastSequenceError:
        return False


async def _release(bucket, *, phase: str, operation_id: str, owner: str) -> None:
    key = operation_lease_key(phase, operation_id)
    try:
        entry = await bucket.get(key)
    except (KeyNotFoundError, KeyDeletedError):
        return
    current = OperationLease.model_validate_json(entry.value)
    if current.owner != owner:
        return
    try:
        await bucket.delete(key, last=entry.revision)
    except KeyWrongLastSequenceError:
        return


@asynccontextmanager
async def operation_leases(
    bucket,
    operation_ids: Iterable[str],
    *,
    phase: str,
    acquire_timeout: float | None = None,
) -> AsyncIterator[OperationLeaseGuard]:
    """Lease operations in stable order and renew them until the work exits.

    PostgreSQL advisory locks remain the commit fence. These leases prevent a
    redelivered message from repeating expensive compute or waiting on that fence.
    """

    identities = sorted(set(operation_ids))
    if not identities:
        yield OperationLeaseGuard(asyncio.Event())
        return
    lease_seconds = CATALOGUE_OPERATION_LEASE_SECONDS
    heartbeat_seconds = CATALOGUE_OPERATION_HEARTBEAT_SECONDS
    if heartbeat_seconds >= lease_seconds:
        raise ValueError("catalogue operation heartbeat must be shorter than its lease")
    timeout = (
        CATALOGUE_OPERATION_ACQUIRE_TIMEOUT_SECONDS
        if acquire_timeout is None
        else acquire_timeout
    )
    owner = uuid4().hex
    acquired: list[str] = []
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        for operation_id in identities:
            if operation_id in acquired:
                continue
            try:
                granted = await _try_acquire(
                    bucket, phase=phase, operation_id=operation_id, owner=owner
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                granted = False
            if granted:
                acquired.append(operation_id)
                continue
            break
        else:
            break
        for operation_id in reversed(acquired):
            try:
                await _release(
                    bucket, phase=phase, operation_id=operation_id, owner=owner
                )
            except Exception:
                # The same owner can renew a partially released set after the
                # broker recovers; TTL remains the final cleanup boundary.
                pass
        acquired.clear()
        if asyncio.get_running_loop().time() >= deadline:
            raise OperationLeaseUnavailable(
                f"catalogue {phase} operation is already active"
            )
        await asyncio.sleep(min(0.1, max(0.01, deadline - asyncio.get_running_loop().time())))

    lost = asyncio.Event()

    async def heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(heartbeat_seconds)
                for operation_id in identities:
                    if not await _try_acquire(
                        bucket, phase=phase, operation_id=operation_id, owner=owner
                    ):
                        lost.set()
                        return
        except asyncio.CancelledError:
            raise
        except Exception:
            # A lease that cannot be renewed is no longer safe to rely on.
            lost.set()

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        yield OperationLeaseGuard(lost)
        if lost.is_set():
            raise OperationLeaseLost(f"catalogue {phase} operation lease was lost")
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        for operation_id in reversed(acquired):
            try:
                await _release(
                    bucket, phase=phase, operation_id=operation_id, owner=owner
                )
            except Exception:
                # The server TTL is the final cleanup boundary after an outage.
                pass
