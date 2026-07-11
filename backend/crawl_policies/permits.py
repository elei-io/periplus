"""Process-local browser concurrency bounds."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

from sqlalchemy.orm import Session
from config import get_float, get_int

from actions.shared.progress import ProgressEvent, ProgressReporter, emit_progress
from crawl_policies.models import CrawlPolicy
from crawl_policies.schemas import CrawlPolicySnapshot

_semaphores: dict[str, tuple[int, asyncio.Semaphore]] = {}


def _limit(policy: CrawlPolicy | CrawlPolicySnapshot | None) -> int | None:
    value = (policy.config or {}).get("max_concurrency") if policy is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


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
        await emit_progress(reporter, ProgressEvent(phase="crawl_capacity", status="waiting", resource=resource, message="Waiting for this worker's browser capacity."))
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
    except TimeoutError as exc:
        raise TimeoutError(f"Timed out waiting for crawl capacity for {resource}.") from exc
    try:
        if waiting:
            await emit_progress(reporter, ProgressEvent(phase="crawl_capacity", status="succeeded", resource=resource, message="Browser capacity acquired."))
        yield
    finally:
        semaphore.release()


@asynccontextmanager
async def capacity_lease(
    session: Session,
    *,
    task_run_id: UUID,
    url: str,
    policy: CrawlPolicy | CrawlPolicySnapshot | None,
    progress_reporter: ProgressReporter | None,
    include_browser: bool = True,
    include_policy: bool = True,
) -> AsyncIterator[None]:
    """Bound crawl concurrency inside one worker process.

    Deployment replicas determine total capacity; no distributed lock is involved.
    """

    del session, task_run_id
    timeout = get_float("ATLAS_CRAWL_PERMIT_TIMEOUT_SECONDS")
    async with AsyncExitStack() as stack:
        if include_browser:
            capacity = get_int("ATLAS_BROWSER_CONCURRENCY")
            await stack.enter_async_context(_slot(_semaphore("browser", capacity), timeout=timeout, resource=url, reporter=progress_reporter))
        policy_limit = _limit(policy)
        if include_policy and policy is not None and policy_limit is not None:
            await stack.enter_async_context(_slot(_semaphore(f"policy:{policy.id}", policy_limit), timeout=timeout, resource=url, reporter=progress_reporter))
        yield
