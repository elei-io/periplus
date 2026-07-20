"""Deployment-wide admission for scarce Atlas resources.

Work delivery, operation deduplication, and durable commit fencing remain separate
concerns.  This module only limits how much pressure may be applied at once.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import math
from typing import Literal
from uuid import uuid4

from config import get_int
from config.performance import (
    OBJECT_IO_UNIT_BYTES,
    RESOURCE_ACQUIRE_TIMEOUT_SECONDS,
    RESOURCE_HEARTBEAT_SECONDS,
    RESOURCE_LEASE_SECONDS,
    RESOURCE_STATE_REPLICAS,
    catalogue_max_concurrency,
    object_io_max_concurrency,
)
from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from observability import resource_metrics


RESOURCE_GRANT_BUCKET = "atlas_resource_grants"
RESOURCE_STATE_KEY = "global"
DURABLE_RESOURCE_WAIT = float("inf")
RESOURCE_RENEW_RETRY_INITIAL_SECONDS = 0.05
RESOURCE_RENEW_RETRY_MAX_SECONDS = 1.0

ResourceClass = Literal["critical", "live", "backfill", "maintenance"]


class ResourceCapacityUnavailable(RuntimeError):
    """The requested resource bundle did not become available in time."""


class ResourcePermitLost(RuntimeError):
    """A previously granted resource bundle could not be renewed."""


class ResourceNeed(BaseModel):
    """One member of an atomically acquired resource bundle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    units: int = Field(ge=1)
    capacity: int | None = Field(default=None, ge=1)


class ResourceRequest(BaseModel):
    """A bounded request for pressure capacity, never durable work state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: str = Field(min_length=1)
    service_class: ResourceClass
    resources: tuple[ResourceNeed, ...]

    @model_validator(mode="after")
    def validate_bundle(self) -> ResourceRequest:
        names = [resource.name for resource in self.resources]
        if not names:
            raise ValueError("a resource request must contain at least one resource")
        if len(set(names)) != len(names):
            raise ValueError("a resource bundle cannot repeat a resource name")
        return self


class ResourceGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str
    operation_id: str
    service_class: ResourceClass
    resources: tuple[ResourceNeed, ...]
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime


class ResourceWaiter(BaseModel):
    """Expiring demand used only to make reserved shares work-conserving."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str
    operation_id: str
    service_class: ResourceClass
    resources: tuple[ResourceNeed, ...]
    waiting_since: datetime
    heartbeat_at: datetime
    expires_at: datetime


class ResourceState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    grants: tuple[ResourceGrant, ...] = ()
    waiters: tuple[ResourceWaiter, ...] = ()
    updated_at: datetime


class ResourceUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    capacity: int
    used: int
    critical: int = 0
    live: int = 0
    backfill: int = 0
    maintenance: int = 0
    waiting: int = 0
    critical_waiting: int = 0
    live_waiting: int = 0
    backfill_waiting: int = 0
    maintenance_waiting: int = 0
    oldest_wait_seconds: float = 0.0


class ResourceLimits(BaseModel):
    """Small deployment policy for shared physical capacity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    catalogue: int = Field(ge=1)
    object_io: int = Field(ge=1)

    @property
    def catalogue_critical_reserve(self) -> int:
        return 1 if self.catalogue >= 2 else 0

    @property
    def catalogue_noncritical_reserve(self) -> int:
        return 1 if self.catalogue >= 2 else 0

    @property
    def catalogue_backfill_max(self) -> int:
        return max(1, self.catalogue // 4)

    @property
    def object_read(self) -> int:
        return self.object_io

    @property
    def object_write(self) -> int:
        return self.object_io

    @classmethod
    def from_env(cls) -> ResourceLimits:
        return cls(
            catalogue=catalogue_max_concurrency(),
            object_io=object_io_max_concurrency(),
        )

    def capacity(self, need: ResourceNeed) -> int:
        configured = {
            "catalogue:hot": self.catalogue,
            "object:read": self.object_read,
            "object:write": self.object_write,
        }.get(need.name)
        if configured is None:
            if need.capacity is None:
                raise ValueError(
                    f"dynamic resource {need.name!r} must declare its capacity"
                )
            return need.capacity
        if need.capacity is not None and need.capacity != configured:
            raise ValueError(
                f"resource {need.name!r} declared capacity {need.capacity}, "
                f"configured as {configured}"
            )
        return configured


class ResourcePermitGuard:
    def __init__(self, lost: asyncio.Event, grant: ResourceGrant) -> None:
        self._lost = lost
        self.grant = grant

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    async def wait_lost(self) -> None:
        await self._lost.wait()


async def ensure_resource_governor_storage(jetstream):
    """Attach or create the single CAS-fenced resource grant bucket."""

    config = KeyValueConfig(
        bucket=RESOURCE_GRANT_BUCKET,
        description="Expiring Atlas shared-resource grants",
        history=1,
        ttl=RESOURCE_LEASE_SECONDS * 2,
        max_bytes=get_int("ATLAS_RESOURCE_GRANT_MAX_BYTES"),
        storage=StorageType.FILE,
        replicas=RESOURCE_STATE_REPLICAS,
    )
    try:
        bucket = await jetstream.key_value(RESOURCE_GRANT_BUCKET)
    except BucketNotFoundError:
        try:
            bucket = await jetstream.create_key_value(config=config)
        except BadRequestError:
            bucket = await jetstream.key_value(RESOURCE_GRANT_BUCKET)
    await _validate_bucket(bucket)
    return bucket


async def _validate_bucket(bucket) -> None:
    status = await bucket.status()
    config = status.stream_info.config
    expected_ttl = RESOURCE_LEASE_SECONDS * 2
    expected_max_bytes = get_int("ATLAS_RESOURCE_GRANT_MAX_BYTES")
    expected_replicas = RESOURCE_STATE_REPLICAS
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
            f"JetStream KV {RESOURCE_GRANT_BUCKET} must use " + ", ".join(mismatches)
        )


def _active_grants(state: ResourceState, *, now: datetime) -> tuple[ResourceGrant, ...]:
    return tuple(grant for grant in state.grants if grant.expires_at > now)


def _active_waiters(
    state: ResourceState, *, now: datetime
) -> tuple[ResourceWaiter, ...]:
    return tuple(waiter for waiter in state.waiters if waiter.expires_at > now)


def _need_for(
    owner: ResourceGrant | ResourceWaiter, name: str
) -> ResourceNeed | None:
    return next((need for need in owner.resources if need.name == name), None)


def _bundle_fits(
    grants: tuple[ResourceGrant, ...],
    waiters: tuple[ResourceWaiter, ...],
    request: ResourceRequest,
    limits: ResourceLimits,
) -> bool:
    for requested in request.resources:
        capacity = limits.capacity(requested)
        if requested.units > capacity:
            raise ValueError(
                f"resource request for {requested.name!r} needs {requested.units} "
                f"units but capacity is {capacity}"
            )
        if request.service_class != "maintenance" and any(
            waiter.service_class == "maintenance"
            and _need_for(waiter, requested.name) is not None
            for waiter in waiters
        ):
            # Exclusive maintenance waits on catalogue and object pressure as
            # one bundle. Stop admitting every overlapping resource so the
            # complete bundle can drain instead of starving on object-only work.
            return False
        existing = [
            (grant, need)
            for grant in grants
            if (need := _need_for(grant, requested.name)) is not None
        ]
        for _grant, need in existing:
            if limits.capacity(need) != capacity:
                # A DomainPolicy limit may change while frozen work from the old
                # revision is still in flight. Drain the old grants before the
                # stable remote-domain resource adopts its new capacity.
                return False
        used = sum(need.units for _grant, need in existing)
        if used + requested.units > capacity:
            return False

        if requested.name == "catalogue:hot":
            critical_used = sum(
                need.units
                for grant, need in existing
                if grant.service_class == "critical"
            )
            noncritical_used = sum(
                need.units
                for grant, need in existing
                if grant.service_class not in {"critical", "maintenance"}
            )
            critical_waiting = any(
                waiter.service_class == "critical"
                and _need_for(waiter, "catalogue:hot") is not None
                for waiter in waiters
            )
            noncritical_waiting = any(
                waiter.service_class in {"live", "backfill"}
                and _need_for(waiter, "catalogue:hot") is not None
                for waiter in waiters
            )
            if request.service_class not in {"critical", "maintenance"}:
                reserved_gap = (
                    max(0, limits.catalogue_critical_reserve - critical_used)
                    if critical_waiting
                    else 0
                )
                if used + requested.units > capacity - reserved_gap:
                    return False
            if request.service_class == "backfill":
                backfill_used = sum(
                    need.units
                    for grant, need in existing
                    if grant.service_class == "backfill"
                )
                if backfill_used + requested.units > limits.catalogue_backfill_max:
                    return False
            if request.service_class == "critical":
                reserved_gap = (
                    max(
                        0,
                        limits.catalogue_noncritical_reserve - noncritical_used,
                    )
                    if noncritical_waiting
                    else 0
                )
                if used + requested.units > capacity - reserved_gap:
                    return False
    return True


async def _read_state(bucket) -> tuple[ResourceState, int | None]:
    try:
        entry = await bucket.get(RESOURCE_STATE_KEY)
    except (KeyNotFoundError, KeyDeletedError):
        return ResourceState(updated_at=datetime.now(UTC)), None
    return ResourceState.model_validate_json(entry.value), entry.revision


async def resource_usage(
    bucket, *, limits: ResourceLimits | None = None
) -> list[ResourceUsage]:
    """Return the current non-authoritative pressure projection for operators."""

    limits = limits or ResourceLimits.from_env()
    state, _revision = await _read_state(bucket)
    now = datetime.now(UTC)
    grants = _active_grants(state, now=now)
    waiters = _active_waiters(state, now=now)
    needs: dict[str, ResourceNeed] = {
        "catalogue:hot": ResourceNeed(name="catalogue:hot", units=1),
        "object:read": ResourceNeed(name="object:read", units=1),
        "object:write": ResourceNeed(name="object:write", units=1),
    }
    for grant in grants:
        for need in grant.resources:
            needs.setdefault(need.name, need)
    for waiter in waiters:
        for need in waiter.resources:
            needs.setdefault(need.name, need)
    usages: list[ResourceUsage] = []
    for name, representative in sorted(needs.items()):
        by_class = {
            service_class: sum(
                need.units
                for grant in grants
                if grant.service_class == service_class
                if (need := _need_for(grant, name)) is not None
            )
            for service_class in ("critical", "live", "backfill", "maintenance")
        }
        waiting_by_class = {
            service_class: sum(
                1
                for waiter in waiters
                if waiter.service_class == service_class
                if _need_for(waiter, name) is not None
            )
            for service_class in ("critical", "live", "backfill", "maintenance")
        }
        resource_waiters = [
            waiter for waiter in waiters if _need_for(waiter, name) is not None
        ]
        usages.append(
            ResourceUsage(
                name=name,
                capacity=limits.capacity(representative),
                used=sum(by_class.values()),
                **by_class,
                waiting=sum(waiting_by_class.values()),
                critical_waiting=waiting_by_class["critical"],
                live_waiting=waiting_by_class["live"],
                backfill_waiting=waiting_by_class["backfill"],
                maintenance_waiting=waiting_by_class["maintenance"],
                oldest_wait_seconds=(
                    max(
                        0.0,
                        (now - min(waiter.waiting_since for waiter in resource_waiters)).total_seconds(),
                    )
                    if resource_waiters
                    else 0.0
                ),
            )
        )
    return usages


async def _write_state(bucket, state: ResourceState, revision: int | None) -> bool:
    payload = state.model_dump_json().encode()
    try:
        if revision is None:
            await bucket.create(RESOURCE_STATE_KEY, payload)
        else:
            await bucket.update(RESOURCE_STATE_KEY, payload, last=revision)
        return True
    except KeyWrongLastSequenceError:
        return False


async def _try_acquire(
    bucket,
    *,
    request: ResourceRequest,
    limits: ResourceLimits,
    token: str,
    now: datetime,
    register_waiter: bool,
) -> ResourceGrant | None:
    state, revision = await _read_state(bucket)
    grants = _active_grants(state, now=now)
    waiters = _active_waiters(state, now=now)
    current = next((grant for grant in grants if grant.token == token), None)
    lease_seconds = RESOURCE_LEASE_SECONDS
    heartbeat_seconds = RESOURCE_HEARTBEAT_SECONDS
    if current is not None:
        renewed = current.model_copy(
            update={
                "heartbeat_at": now,
                "expires_at": now + timedelta(seconds=lease_seconds),
            }
        )
        grants = tuple(renewed if grant.token == token else grant for grant in grants)
        updated = ResourceState(grants=grants, waiters=waiters, updated_at=now)
        return renewed if await _write_state(bucket, updated, revision) else None

    current_waiter = next(
        (waiter for waiter in waiters if waiter.token == token), None
    )
    other_waiters = tuple(waiter for waiter in waiters if waiter.token != token)
    if not _bundle_fits(grants, other_waiters, request, limits):
        if not register_waiter:
            return None
        should_refresh = (
            current_waiter is None
            or (now - current_waiter.heartbeat_at).total_seconds()
            >= heartbeat_seconds
        )
        if should_refresh:
            waiter = ResourceWaiter(
                token=token,
                operation_id=request.operation_id,
                service_class=request.service_class,
                resources=request.resources,
                waiting_since=(
                    current_waiter.waiting_since
                    if current_waiter is not None
                    else now
                ),
                heartbeat_at=now,
                expires_at=now + timedelta(seconds=lease_seconds),
            )
            waiters = (*other_waiters, waiter)
        if (
            grants != state.grants
            or waiters != state.waiters
        ):
            await _write_state(
                bucket,
                ResourceState(grants=grants, waiters=waiters, updated_at=now),
                revision,
            )
        return None
    grant = ResourceGrant(
        token=token,
        operation_id=request.operation_id,
        service_class=request.service_class,
        resources=request.resources,
        acquired_at=now,
        heartbeat_at=now,
        expires_at=now + timedelta(seconds=lease_seconds),
    )
    updated = ResourceState(
        grants=(*grants, grant),
        waiters=other_waiters,
        updated_at=now,
    )
    return grant if await _write_state(bucket, updated, revision) else None


async def _renew_grant(
    bucket,
    *,
    request: ResourceRequest,
    limits: ResourceLimits,
    token: str,
    grant: ResourceGrant,
) -> ResourceGrant | None:
    """Retry transient renewal contention while the current lease is safe."""

    heartbeat_seconds = RESOURCE_HEARTBEAT_SECONDS
    loop = asyncio.get_running_loop()
    now = datetime.now(UTC)
    safe_remaining = (
        grant.expires_at - now
    ).total_seconds() - heartbeat_seconds
    retry_deadline = loop.time() + min(heartbeat_seconds, max(0.0, safe_remaining))
    retry_delay = RESOURCE_RENEW_RETRY_INITIAL_SECONDS
    while True:
        now = datetime.now(UTC)
        safe_remaining = (
            grant.expires_at - now
        ).total_seconds() - heartbeat_seconds
        retry_remaining = retry_deadline - loop.time()
        if safe_remaining <= 0 or retry_remaining <= 0:
            return None
        try:
            renewed = await _try_acquire(
                bucket,
                request=request,
                limits=limits,
                token=token,
                now=now,
                register_waiter=False,
            )
        except asyncio.CancelledError:
            raise
        except ValueError:
            raise
        except Exception:
            renewed = None
        if renewed is not None:
            return renewed
        await asyncio.sleep(min(retry_delay, safe_remaining, retry_remaining))
        retry_delay = min(
            retry_delay * 2,
            RESOURCE_RENEW_RETRY_MAX_SECONDS,
        )


async def _release(bucket, *, token: str) -> None:
    while True:
        state, revision = await _read_state(bucket)
        grants = tuple(grant for grant in state.grants if grant.token != token)
        waiters = tuple(waiter for waiter in state.waiters if waiter.token != token)
        if grants == state.grants and waiters == state.waiters:
            return
        if await _write_state(
            bucket,
            ResourceState(
                grants=grants,
                waiters=waiters,
                updated_at=datetime.now(UTC),
            ),
            revision,
        ):
            return


@asynccontextmanager
async def resource_permits(
    bucket,
    request: ResourceRequest,
    *,
    limits: ResourceLimits | None = None,
    acquire_timeout: float | None = None,
) -> AsyncIterator[ResourcePermitGuard]:
    """Atomically lease a resource bundle and renew it until the operation exits."""

    limits = limits or ResourceLimits.from_env()
    lease_seconds = RESOURCE_LEASE_SECONDS
    heartbeat_seconds = RESOURCE_HEARTBEAT_SECONDS
    if heartbeat_seconds >= lease_seconds:
        raise ValueError("resource heartbeat must be shorter than its lease")
    timeout = (
        RESOURCE_ACQUIRE_TIMEOUT_SECONDS
        if acquire_timeout is None
        else acquire_timeout
    )
    token = uuid4().hex
    loop = asyncio.get_running_loop()
    wait_started = loop.time()
    deadline = wait_started + timeout
    grant: ResourceGrant | None = None
    try:
        while grant is None:
            try:
                grant = await _try_acquire(
                    bucket,
                    request=request,
                    limits=limits,
                    token=token,
                    now=datetime.now(UTC),
                    register_waiter=timeout > 0,
                )
            except asyncio.CancelledError:
                raise
            except ValueError:
                raise
            except Exception as exc:
                if loop.time() >= deadline:
                    resource_metrics.admission(
                        request,
                        outcome="unavailable",
                        wait_seconds=loop.time() - wait_started,
                    )
                    raise ResourceCapacityUnavailable(
                        f"resource governor unavailable for {request.operation_id}"
                    ) from exc
                await asyncio.sleep(0.1)
                continue
            if grant is not None:
                break
            if loop.time() >= deadline:
                resource_metrics.admission(
                    request,
                    outcome="unavailable",
                    wait_seconds=loop.time() - wait_started,
                )
                raise ResourceCapacityUnavailable(
                    f"resource capacity unavailable for {request.operation_id}"
                )
            await asyncio.sleep(min(0.1, max(0.01, deadline - loop.time())))
    except BaseException:
        try:
            await _release(bucket, token=token)
        except Exception:
            pass
        raise

    acquired_at = loop.time()
    resource_metrics.admission(
        request,
        outcome="granted",
        wait_seconds=acquired_at - wait_started,
    )

    lost = asyncio.Event()

    async def heartbeat() -> None:
        current_grant = grant
        try:
            while True:
                await asyncio.sleep(heartbeat_seconds)
                renewed = await _renew_grant(
                    bucket,
                    request=request,
                    limits=limits,
                    token=token,
                    grant=current_grant,
                )
                if renewed is None:
                    lost.set()
                    return
                current_grant = renewed
        except asyncio.CancelledError:
            raise
        except Exception:
            lost.set()

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        yield ResourcePermitGuard(lost, grant)
        if lost.is_set():
            raise ResourcePermitLost(
                f"resource permit was lost for {request.operation_id}"
            )
    finally:
        resource_metrics.hold(
            request,
            outcome="lost" if lost.is_set() else "released",
            hold_seconds=loop.time() - acquired_at,
        )
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        try:
            await _release(bucket, token=token)
        except Exception:
            # Expiry is the final recovery boundary after a broker outage.
            pass


def catalogue_request(
    operation_id: str,
    *,
    service_class: ResourceClass,
    object_read_units: int = 0,
    object_write_units: int = 0,
    limits: ResourceLimits | None = None,
    exclusive: bool = False,
) -> ResourceRequest:
    """Build the fixed bundle used by DuckLake-owning workers."""

    limits = limits or ResourceLimits.from_env()
    resources = [
        ResourceNeed(
            name="catalogue:hot",
            units=limits.catalogue if exclusive else 1,
        )
    ]
    if object_read_units:
        if object_read_units > limits.object_read:
            raise ValueError("object-read request exceeds configured capacity")
        resources.append(ResourceNeed(name="object:read", units=object_read_units))
    if object_write_units:
        if object_write_units > limits.object_write:
            raise ValueError("object-write request exceeds configured capacity")
        resources.append(ResourceNeed(name="object:write", units=object_write_units))
    return ResourceRequest(
        operation_id=operation_id,
        service_class=service_class,
        resources=tuple(resources),
    )


def remote_request(
    operation_id: str,
    *,
    remote_domain: str,
    concurrency: int,
) -> ResourceRequest:
    return ResourceRequest(
        operation_id=operation_id,
        service_class="critical",
        resources=(
            ResourceNeed(
                name=f"remote:{remote_domain}",
                units=1,
                capacity=concurrency,
            ),
        ),
    )


def object_units(byte_count: int) -> int:
    """Convert known transfer bytes into stable weighted admission units."""

    if byte_count < 0:
        raise ValueError("object byte count cannot be negative")
    return max(1, math.ceil(byte_count / OBJECT_IO_UNIT_BYTES))


def object_request(
    operation_id: str,
    *,
    direction: Literal["read", "write"],
    byte_count: int,
    service_class: ResourceClass,
) -> ResourceRequest:
    limits = ResourceLimits.from_env()
    units = object_units(byte_count)
    capacity = limits.object_read if direction == "read" else limits.object_write
    if units > capacity:
        raise ValueError(
            f"object-{direction} request needs {units} units but capacity is {capacity}"
        )
    return ResourceRequest(
        operation_id=operation_id,
        service_class=service_class,
        resources=(
            ResourceNeed(
                name=f"object:{direction}",
                units=units,
            ),
        ),
    )
