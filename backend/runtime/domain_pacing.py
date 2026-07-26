"""Per-domain concurrency, minimum-interval reservations, and adaptive backoff."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)
from pydantic import BaseModel, ConfigDict

from config import get_int
from config.performance import (
    DOMAIN_PERMIT_HEARTBEAT_SECONDS,
    DOMAIN_PERMIT_LEASE_SECONDS,
    OPERATIONAL_STATE_REPLICAS,
)

DOMAIN_PACING_BUCKET = "atlas_domain_pacing"


class DomainCapacityUnavailable(RuntimeError):
    """The hostname has reached its deployment-wide concurrency limit."""


class DomainPermitLost(RuntimeError):
    """A previously acquired hostname slot could not be renewed."""


class DomainConcurrencyGrant(BaseModel):
    model_config = ConfigDict(frozen=True)

    token: str
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime


class DomainPacingState(BaseModel):
    model_config = ConfigDict(frozen=True)

    next_admission_at: datetime
    blocked_until: datetime | None = None
    transient_failure_count: int = 0
    last_failure_at: datetime | None = None
    concurrency_grants: tuple[DomainConcurrencyGrant, ...] = ()


class DomainPermitGuard:
    def __init__(self, lost: asyncio.Event) -> None:
        self._lost = lost

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    async def wait_lost(self) -> None:
        await self._lost.wait()


async def ensure_domain_pacing_storage(jetstream):
    config = KeyValueConfig(
        bucket=DOMAIN_PACING_BUCKET,
        description="Atlas per-domain politeness pacing and concurrency",
        history=1,
        max_bytes=get_int("ATLAS_DOMAIN_PACING_MAX_BYTES"),
        storage=StorageType.FILE,
        replicas=OPERATIONAL_STATE_REPLICAS,
    )
    try:
        bucket = await jetstream.key_value(DOMAIN_PACING_BUCKET)
    except BucketNotFoundError:
        try:
            bucket = await jetstream.create_key_value(config=config)
        except BadRequestError:
            bucket = await jetstream.key_value(DOMAIN_PACING_BUCKET)
    status = await bucket.status()
    actual = status.stream_info.config
    if (
        actual.storage != StorageType.FILE
        or actual.max_msgs_per_subject != 1
        or actual.max_bytes != config.max_bytes
        or actual.num_replicas != config.replicas
    ):
        raise RuntimeError(
            f"JetStream KV {DOMAIN_PACING_BUCKET} has an incompatible contract"
        )
    return bucket


def _active_grants(
    state: DomainPacingState, *, now: datetime
) -> tuple[DomainConcurrencyGrant, ...]:
    return tuple(
        grant for grant in state.concurrency_grants if grant.expires_at > now
    )


async def _read_state(bucket, key: str) -> tuple[DomainPacingState, int | None]:
    try:
        entry = await bucket.get(key)
    except (KeyNotFoundError, KeyDeletedError):
        return DomainPacingState(next_admission_at=datetime.now(UTC)), None
    return DomainPacingState.model_validate_json(entry.value), entry.revision


async def _write_state(
    bucket,
    key: str,
    state: DomainPacingState,
    revision: int | None,
) -> bool:
    payload = state.model_dump_json().encode()
    try:
        if revision is None:
            await bucket.create(key, payload)
        else:
            await bucket.update(key, payload, last=revision)
        return True
    except KeyWrongLastSequenceError:
        return False


async def _try_acquire_domain(
    bucket,
    *,
    key: str,
    token: str,
    concurrency: int,
) -> DomainConcurrencyGrant | None:
    now = datetime.now(UTC)
    state, revision = await _read_state(bucket, key)
    grants = _active_grants(state, now=now)
    current = next((grant for grant in grants if grant.token == token), None)
    if current is not None:
        renewed = current.model_copy(
            update={
                "heartbeat_at": now,
                "expires_at": now + timedelta(seconds=DOMAIN_PERMIT_LEASE_SECONDS),
            }
        )
        grants = tuple(renewed if grant.token == token else grant for grant in grants)
        updated = state.model_copy(update={"concurrency_grants": grants})
        return renewed if await _write_state(bucket, key, updated, revision) else None
    if len(grants) >= concurrency:
        if grants != state.concurrency_grants:
            await _write_state(
                bucket,
                key,
                state.model_copy(update={"concurrency_grants": grants}),
                revision,
            )
        return None
    grant = DomainConcurrencyGrant(
        token=token,
        acquired_at=now,
        heartbeat_at=now,
        expires_at=now + timedelta(seconds=DOMAIN_PERMIT_LEASE_SECONDS),
    )
    updated = state.model_copy(update={"concurrency_grants": (*grants, grant)})
    return grant if await _write_state(bucket, key, updated, revision) else None


async def _release_domain(bucket, *, key: str, token: str) -> None:
    for _attempt in range(10):
        state, revision = await _read_state(bucket, key)
        grants = tuple(
            grant
            for grant in _active_grants(state, now=datetime.now(UTC))
            if grant.token != token
        )
        if grants == state.concurrency_grants:
            return
        if await _write_state(
            bucket,
            key,
            state.model_copy(update={"concurrency_grants": grants}),
            revision,
        ):
            return
        await asyncio.sleep(0)
    # Expiry is the final recovery boundary under sustained CAS contention.


@asynccontextmanager
async def domain_permit(
    bucket,
    *,
    domain: str,
    concurrency: int,
    acquire_timeout: float | None = None,
) -> AsyncIterator[DomainPermitGuard]:
    """Acquire one expiring slot scoped only to one hostname."""

    if concurrency <= 0:
        raise ValueError("domain concurrency must be greater than zero")
    if DOMAIN_PERMIT_HEARTBEAT_SECONDS >= DOMAIN_PERMIT_LEASE_SECONDS:
        raise ValueError("domain permit heartbeat must be shorter than its lease")
    token = uuid4().hex
    key = _domain_key(domain)
    loop = asyncio.get_running_loop()
    deadline = (
        None if acquire_timeout is None else loop.time() + acquire_timeout
    )
    while True:
        grant = await _try_acquire_domain(
            bucket,
            key=key,
            token=token,
            concurrency=concurrency,
        )
        if grant is not None:
            break
        if deadline is not None and loop.time() >= deadline:
            raise DomainCapacityUnavailable(
                f"domain concurrency is busy for {domain}"
            )
        await asyncio.sleep(0.05)

    lost = asyncio.Event()

    async def renew() -> None:
        while True:
            await asyncio.sleep(DOMAIN_PERMIT_HEARTBEAT_SECONDS)
            for _attempt in range(10):
                try:
                    renewed = await _try_acquire_domain(
                        bucket,
                        key=key,
                        token=token,
                        concurrency=concurrency,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    renewed = None
                if renewed is not None:
                    break
                await asyncio.sleep(0.05)
            else:
                lost.set()
                return

    heartbeat = asyncio.create_task(renew())
    try:
        guard = DomainPermitGuard(lost)
        yield guard
        if guard.lost:
            raise DomainPermitLost(f"domain permit was lost for {domain}")
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            await _release_domain(bucket, key=key, token=token)
        except Exception:
            # The grant expires if NATS is temporarily unavailable.
            pass


async def wait_for_domain_interval(
    bucket,
    *,
    domain: str,
    interval_seconds: float,
) -> None:
    """Atomically reserve the next domain admission time, then wait for it.

    The reservation observes both the frozen policy interval and any adaptive
    response backoff recorded by another acquisition worker.
    """

    interval_seconds = max(0.0, interval_seconds)
    key = _domain_key(domain)
    while True:
        now = datetime.now(UTC)
        try:
            entry = await bucket.get(key)
        except KeyNotFoundError:
            if interval_seconds == 0:
                return
            state = DomainPacingState(
                next_admission_at=now + timedelta(seconds=interval_seconds)
            )
            try:
                await bucket.create(key, state.model_dump_json().encode())
                return
            except KeyWrongLastSequenceError:
                continue
        current = DomainPacingState.model_validate_json(entry.value)
        admitted_at = max(
            now,
            current.next_admission_at,
            current.blocked_until or now,
        )
        if interval_seconds == 0:
            delay = (admitted_at - now).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            return
        state = current.model_copy(
            update={
                "next_admission_at": admitted_at
                + timedelta(seconds=interval_seconds)
            }
        )
        try:
            await bucket.update(
                key,
                state.model_dump_json().encode(),
                last=entry.revision,
            )
        except KeyWrongLastSequenceError:
            continue
        delay = (admitted_at - now).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        return


async def domain_backoff_seconds(bucket, *, domain: str) -> float:
    """Return the deployment-wide response backoff still active for a domain."""

    try:
        entry = await bucket.get(_domain_key(domain))
    except KeyNotFoundError:
        return 0.0
    state = DomainPacingState.model_validate_json(entry.value)
    if state.blocked_until is None:
        return 0.0
    return max(0.0, (state.blocked_until - datetime.now(UTC)).total_seconds())


async def record_domain_response(
    bucket,
    *,
    domain: str,
    status_code: int | None,
    retry_after_seconds: float | None = None,
) -> float:
    """Update shared domain health from one HTTP response.

    A 429 immediately cools the hostname down. Repeated 5xx responses trigger
    a weaker circuit breaker so one broken URL does not suppress a healthy
    hostname. Successful HTTP responses decay the failure score. Transport
    failures have no status and remain provider/worker signals rather than
    website-health evidence.
    """

    if status_code is None:
        return 0.0
    key = _domain_key(domain)
    while True:
        now = datetime.now(UTC)
        try:
            entry = await bucket.get(key)
        except KeyNotFoundError:
            if status_code != 429 and not 500 <= status_code <= 599:
                return 0.0
            current = DomainPacingState(next_admission_at=now)
            revision = None
        else:
            current = DomainPacingState.model_validate_json(entry.value)
            revision = entry.revision

        updated = _response_transition(
            current,
            now=now,
            status_code=status_code,
            retry_after_seconds=retry_after_seconds,
        )
        if updated == current:
            return max(
                0.0,
                ((current.blocked_until or now) - now).total_seconds(),
            )
        payload = updated.model_dump_json().encode()
        try:
            if revision is None:
                await bucket.create(key, payload)
            else:
                await bucket.update(key, payload, last=revision)
        except KeyWrongLastSequenceError:
            continue
        return max(
            0.0,
            ((updated.blocked_until or now) - now).total_seconds(),
        )


def _response_transition(
    current: DomainPacingState,
    *,
    now: datetime,
    status_code: int,
    retry_after_seconds: float | None,
) -> DomainPacingState:
    if status_code == 429 or 500 <= status_code <= 599:
        recent = (
            current.last_failure_at is not None
            and (now - current.last_failure_at).total_seconds() <= 60
        )
        failures = current.transient_failure_count + 1 if recent else 1
        delay = 0.0
        if retry_after_seconds is not None and retry_after_seconds > 0:
            delay = retry_after_seconds
        elif status_code == 429:
            delay = min(15 * 60.0, 5.0 * (2 ** min(failures - 1, 8)))
        elif failures >= 3:
            delay = min(5 * 60.0, 2.0 ** min(failures - 3, 8))
        blocked_until = current.blocked_until
        if delay > 0:
            candidate = now + timedelta(seconds=delay)
            blocked_until = max(blocked_until or candidate, candidate)
        return current.model_copy(
            update={
                "blocked_until": blocked_until,
                "transient_failure_count": failures,
                "last_failure_at": now,
            }
        )

    failures = max(0, current.transient_failure_count - 1)
    return current.model_copy(
        update={
            "transient_failure_count": failures,
            "last_failure_at": current.last_failure_at if failures else None,
        }
    )


def _domain_key(domain: str) -> str:
    return "domain-" + hashlib.sha256(domain.lower().encode()).hexdigest()
