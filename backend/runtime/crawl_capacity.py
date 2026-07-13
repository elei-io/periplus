"""Deployment-wide policy pressure and process-local acquisition safety."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import AsyncIterator
from uuid import UUID, uuid4

from nats.js.errors import (
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from actions.shared.progress import ProgressEvent, ProgressReporter, emit_progress
from config import get_float, get_int
from control.crawl_policies.models import CrawlPolicy
from control.crawl_policies.schemas import CrawlPolicyConfig, CrawlPolicySnapshot

_semaphores: dict[str, tuple[int, asyncio.Semaphore]] = {}


class PolicyCapacityHolder(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner: UUID
    expires_at: datetime


class PolicyCapacityState(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: UUID
    policy_revision: int = Field(ge=1)
    concurrency: int = Field(ge=1)
    holders: tuple[PolicyCapacityHolder, ...] = ()
    updated_at: datetime


def policy_capacity_key(policy_id: UUID, revision: int) -> str:
    return f"policy-{policy_id.hex}-r{revision}"


def _policy_envelope(
    policy: CrawlPolicy | CrawlPolicySnapshot,
) -> CrawlPolicyConfig:
    return CrawlPolicyConfig.model_validate(policy.config or {})


def _semaphore(key: str, capacity: int) -> asyncio.Semaphore:
    current = _semaphores.get(key)
    if current is None or current[0] != capacity:
        current = (capacity, asyncio.Semaphore(capacity))
        _semaphores[key] = current
    return current[1]


@asynccontextmanager
async def _slot(
    semaphore: asyncio.Semaphore,
    *,
    timeout: float,
    resource: str,
    reporter: ProgressReporter | None,
) -> AsyncIterator[None]:
    waiting = semaphore.locked()
    if waiting:
        await emit_progress(
            reporter,
            ProgressEvent(
                phase="crawl_capacity",
                status="waiting",
                resource=resource,
                message="Waiting for this worker's browser capacity.",
            ),
        )
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
    except TimeoutError as exc:
        raise TimeoutError(f"Timed out waiting for crawl capacity for {resource}.") from exc
    try:
        if waiting:
            await emit_progress(
                reporter,
                ProgressEvent(
                    phase="crawl_capacity",
                    status="succeeded",
                    resource=resource,
                    message="Browser capacity acquired.",
                ),
            )
        yield
    finally:
        semaphore.release()


def _active_holders(
    state: PolicyCapacityState,
    *,
    now: datetime,
) -> tuple[PolicyCapacityHolder, ...]:
    return tuple(holder for holder in state.holders if holder.expires_at > now)


async def _try_acquire_policy_slot(
    bucket,
    *,
    policy: CrawlPolicy | CrawlPolicySnapshot,
    owner: UUID,
    now: datetime,
) -> bool:
    envelope = _policy_envelope(policy)
    key = policy_capacity_key(policy.id, policy.revision)
    expires_at = now + timedelta(seconds=get_float("ATLAS_CRAWL_POLICY_LEASE_SECONDS"))
    holder = PolicyCapacityHolder(owner=owner, expires_at=expires_at)
    try:
        entry = await bucket.get(key)
    except (KeyNotFoundError, KeyDeletedError):
        state = PolicyCapacityState(
            policy_id=policy.id,
            policy_revision=policy.revision,
            concurrency=envelope.concurrency,
            holders=(holder,),
            updated_at=now,
        )
        try:
            await bucket.create(key, state.model_dump_json().encode())
            return True
        except KeyWrongLastSequenceError:
            return False

    state = PolicyCapacityState.model_validate_json(entry.value)
    if state.concurrency != envelope.concurrency:
        raise RuntimeError(
            f"policy {policy.id} revision {policy.revision} has conflicting concurrency"
        )
    holders = _active_holders(state, now=now)
    existing = next((value for value in holders if value.owner == owner), None)
    if existing is None and len(holders) >= state.concurrency:
        if holders != state.holders:
            updated = state.model_copy(update={"holders": holders, "updated_at": now})
            try:
                await bucket.update(key, updated.model_dump_json().encode(), last=entry.revision)
            except KeyWrongLastSequenceError:
                pass
        return False
    holders = tuple(value for value in holders if value.owner != owner) + (holder,)
    updated = state.model_copy(update={"holders": holders, "updated_at": now})
    try:
        await bucket.update(key, updated.model_dump_json().encode(), last=entry.revision)
        return True
    except KeyWrongLastSequenceError:
        return False


async def _release_policy_slot(
    bucket,
    *,
    policy: CrawlPolicy | CrawlPolicySnapshot,
    owner: UUID,
) -> None:
    key = policy_capacity_key(policy.id, policy.revision)
    while True:
        try:
            entry = await bucket.get(key)
        except (KeyNotFoundError, KeyDeletedError):
            return
        state = PolicyCapacityState.model_validate_json(entry.value)
        holders = tuple(value for value in state.holders if value.owner != owner)
        if holders == state.holders:
            return
        updated = state.model_copy(
            update={"holders": holders, "updated_at": datetime.now(UTC)}
        )
        try:
            await bucket.update(key, updated.model_dump_json().encode(), last=entry.revision)
            return
        except KeyWrongLastSequenceError:
            continue


@asynccontextmanager
async def _policy_slot(
    bucket,
    *,
    policy: CrawlPolicy | CrawlPolicySnapshot,
    owner: UUID,
    timeout: float,
    resource: str,
    reporter: ProgressReporter | None,
) -> AsyncIterator[None]:
    lease_seconds = get_float("ATLAS_CRAWL_POLICY_LEASE_SECONDS")
    heartbeat_seconds = get_float("ATLAS_CRAWL_POLICY_HEARTBEAT_SECONDS")
    if heartbeat_seconds >= lease_seconds:
        raise ValueError(
            "ATLAS_CRAWL_POLICY_HEARTBEAT_SECONDS must be shorter than "
            "ATLAS_CRAWL_POLICY_LEASE_SECONDS"
        )
    started = asyncio.get_running_loop().time()
    waiting_reported = False
    while not await _try_acquire_policy_slot(
        bucket, policy=policy, owner=owner, now=datetime.now(UTC)
    ):
        if not waiting_reported:
            waiting_reported = True
            await emit_progress(
                reporter,
                ProgressEvent(
                    phase="crawl_capacity",
                    status="waiting",
                    resource=resource,
                    message="Waiting for deployment-wide CrawlPolicy capacity.",
                ),
            )
        if asyncio.get_running_loop().time() - started >= timeout:
            raise TimeoutError(f"Timed out waiting for CrawlPolicy capacity for {resource}.")
        await asyncio.sleep(min(0.25, max(0.01, timeout / 20)))

    owner_task = asyncio.current_task()

    async def heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(heartbeat_seconds)
                deadline = asyncio.get_running_loop().time() + (
                    lease_seconds - heartbeat_seconds
                )
                while not await _try_acquire_policy_slot(
                    bucket, policy=policy, owner=owner, now=datetime.now(UTC)
                ):
                    if asyncio.get_running_loop().time() >= deadline:
                        raise RuntimeError("Lost the deployment-wide CrawlPolicy lease")
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception:
            if owner_task is not None:
                owner_task.cancel()
            raise

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        if waiting_reported:
            await emit_progress(
                reporter,
                ProgressEvent(
                    phase="crawl_capacity",
                    status="succeeded",
                    resource=resource,
                    message="Deployment-wide CrawlPolicy capacity acquired.",
                ),
            )
        yield
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        try:
            await _release_policy_slot(bucket, policy=policy, owner=owner)
        except Exception:
            # The lease is self-expiring; a release outage must not replace the
            # acquisition outcome with a second error.
            pass


@asynccontextmanager
async def capacity_lease(
    session: Session | None,
    *,
    url: str,
    policy: CrawlPolicy | CrawlPolicySnapshot | None,
    progress_reporter: ProgressReporter | None,
    include_browser: bool = True,
    capacity_bucket=None,
    owner: UUID | None = None,
) -> AsyncIterator[None]:
    """Bound policy pressure globally and browser pressure inside one worker."""

    del session
    timeout = get_float("ATLAS_CRAWL_PERMIT_TIMEOUT_SECONDS")
    async with AsyncExitStack() as stack:
        if policy is not None and capacity_bucket is not None:
            await stack.enter_async_context(
                _policy_slot(
                    capacity_bucket,
                    policy=policy,
                    owner=owner or uuid4(),
                    timeout=timeout,
                    resource=url,
                    reporter=progress_reporter,
                )
            )
        if include_browser:
            await stack.enter_async_context(
                _slot(
                    _semaphore("browser", get_int("ATLAS_BROWSER_CONCURRENCY")),
                    timeout=timeout,
                    resource=url,
                    reporter=progress_reporter,
                )
            )
        yield
