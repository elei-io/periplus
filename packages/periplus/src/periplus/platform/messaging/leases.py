"""Expiring NATS leases for retry-stable catalogue operations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from periplus.platform.config import get_int
from periplus.platform.config.performance import (
    CATALOGUE_OPERATION_ACQUIRE_TIMEOUT_SECONDS,
    CATALOGUE_OPERATION_HEARTBEAT_SECONDS,
    CATALOGUE_OPERATION_LEASE_SECONDS,
)
from periplus.platform.messaging.topology import operational_replicas

from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)
from pydantic import BaseModel, ConfigDict
from periplus.platform.messaging.topology import validate_kv_contract

OPERATION_LEASE_BUCKET = "periplus_catalog_operations"
_LEASE_RENEWAL_CONCURRENCY = 32


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
        description="Expiring Periplus catalogue operation leases",
        history=1,
        ttl=CATALOGUE_OPERATION_LEASE_SECONDS,
        max_bytes=get_int("PERIPLUS_OPERATION_LEASE_MAX_BYTES"),
        storage=StorageType.FILE,
        replicas=operational_replicas(),
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
    await validate_kv_contract(
        bucket,
        name=OPERATION_LEASE_BUCKET,
        ttl=CATALOGUE_OPERATION_LEASE_SECONDS,
        max_bytes=get_int("PERIPLUS_OPERATION_LEASE_MAX_BYTES"),
        replicas=operational_replicas(),
    )


async def _try_acquire(
    bucket,
    *,
    phase: str,
    operation_id: str,
    owner: str,
    create_first: bool = True,
) -> bool:
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
    if create_first:
        try:
            await bucket.create(key, lease.model_dump_json().encode())
            return True
        except KeyWrongLastSequenceError:
            pass

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

    These leases prevent redelivered messages or horizontal replicas from
    repeating the same expensive durable operation.
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
    lost = asyncio.Event()

    async def heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(heartbeat_seconds)
                # Acquisition can itself be long when one commit introduces
                # thousands of shared URL identities. Renew the stable prefix
                # already owned instead of waiting for the full set.
                owned = tuple(acquired)
                for offset in range(0, len(owned), _LEASE_RENEWAL_CONCURRENCY):
                    renewed = await asyncio.gather(
                        *(
                            _try_acquire(
                                bucket,
                                phase=phase,
                                operation_id=operation_id,
                                owner=owner,
                                create_first=False,
                            )
                            for operation_id in owned[
                                offset : offset + _LEASE_RENEWAL_CONCURRENCY
                            ]
                        )
                    )
                    if not all(renewed):
                        lost.set()
                        return
        except asyncio.CancelledError:
            raise
        except Exception:
            # A lease that cannot be renewed is no longer safe to rely on.
            lost.set()

    heartbeat_task = asyncio.create_task(heartbeat())

    async def release_acquired() -> None:
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
        acquired.clear()

    try:
        while True:
            for operation_id in identities:
                if operation_id in acquired:
                    continue
                if lost.is_set():
                    break
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
            await release_acquired()
            if asyncio.get_running_loop().time() >= deadline:
                raise OperationLeaseUnavailable(
                    f"catalogue {phase} operation is already active"
                )
            await asyncio.sleep(
                min(
                    0.1,
                    max(
                        0.01,
                        deadline - asyncio.get_running_loop().time(),
                    ),
                )
            )
            lost = asyncio.Event()
            heartbeat_task = asyncio.create_task(heartbeat())

        yield OperationLeaseGuard(lost)
        if lost.is_set():
            raise OperationLeaseLost(f"catalogue {phase} operation lease was lost")
    finally:
        await release_acquired()
