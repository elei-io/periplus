"""Distributed minimum-interval reservations and adaptive domain backoff."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import BadRequestError, BucketNotFoundError, KeyNotFoundError, KeyWrongLastSequenceError
from pydantic import BaseModel, ConfigDict

from config import get_int
from config.performance import RESOURCE_STATE_REPLICAS

DOMAIN_PACING_BUCKET = "atlas_domain_pacing"


class DomainPacingState(BaseModel):
    model_config = ConfigDict(frozen=True)

    next_admission_at: datetime
    blocked_until: datetime | None = None
    transient_failure_count: int = 0
    last_failure_at: datetime | None = None


async def ensure_domain_pacing_storage(jetstream):
    config = KeyValueConfig(
        bucket=DOMAIN_PACING_BUCKET,
        description="Atlas per-domain politeness pacing",
        history=1,
        max_bytes=get_int("ATLAS_DOMAIN_PACING_MAX_BYTES"),
        storage=StorageType.FILE,
        replicas=RESOURCE_STATE_REPLICAS,
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
