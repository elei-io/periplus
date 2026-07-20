"""Distributed minimum-interval reservations for domain politeness."""

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


async def wait_for_domain_interval(bucket, *, domain: str, interval_seconds: float) -> None:
    """Atomically reserve the next domain admission time, then wait for it."""

    if interval_seconds <= 0:
        return
    key = "domain-" + hashlib.sha256(domain.encode()).hexdigest()
    while True:
        now = datetime.now(UTC)
        try:
            entry = await bucket.get(key)
        except KeyNotFoundError:
            state = DomainPacingState(next_admission_at=now + timedelta(seconds=interval_seconds))
            try:
                await bucket.create(key, state.model_dump_json().encode())
                return
            except KeyWrongLastSequenceError:
                continue
        current = DomainPacingState.model_validate_json(entry.value)
        admitted_at = max(now, current.next_admission_at)
        state = DomainPacingState(next_admission_at=admitted_at + timedelta(seconds=interval_seconds))
        try:
            await bucket.update(key, state.model_dump_json().encode(), last=entry.revision)
        except KeyWrongLastSequenceError:
            continue
        delay = (admitted_at - now).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        return
