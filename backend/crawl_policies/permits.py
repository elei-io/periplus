from __future__ import annotations

import asyncio
import os
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from actions.shared.progress import ProgressReporter, ProgressEvent, emit_progress
from observability import capacity_metrics
from tasks.models import TaskRun, TaskRunLease
from tasks.context import current_task_execution
from .models import CrawlPermit, CrawlPolicy
from .schemas import CrawlPolicySnapshot

_RELEASE_CHANNEL = "atlas_crawl_permits_released"


class _PermitReleaseListener:
    def __init__(self, session_factory: sessionmaker) -> None:
        engine = session_factory.kw["bind"]
        self._connection_string = str(engine.url).replace("postgresql+psycopg://", "postgresql://", 1)
        self._connection: psycopg.AsyncConnection | None = None

    async def wait(self, timeout: float) -> None:
        try:
            if self._connection is None or self._connection.closed:
                self._connection = await psycopg.AsyncConnection.connect(
                    self._connection_string, autocommit=True, connect_timeout=1
                )
                await self._connection.execute(f"LISTEN {_RELEASE_CHANNEL}")
            async for _ in self._connection.notifies(timeout=timeout, stop_after=1):
                return
        except Exception:
            await self.close()
            await asyncio.sleep(timeout)

    async def close(self) -> None:
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:
                pass
            self._connection = None


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _policy_limit(policy: CrawlPolicy | CrawlPolicySnapshot | None) -> int | None:
    if policy is None:
        return None
    raw = (policy.config or {}).get("max_concurrency")
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, str) and raw.isdigit() and int(raw) > 0:
        return int(raw)
    return None


def _try_acquire(
    session: Session,
    *,
    permit_key: str,
    capacity: int,
    task_run_id: UUID,
    worker_id: str,
    policy_id: UUID | None,
    lease_seconds: int,
) -> CrawlPermit | None:
    now = datetime.now(UTC)
    # Serialize only the short allocation transaction, never the page load.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": permit_key})
    session.execute(
        delete(CrawlPermit).where(
            CrawlPermit.permit_key == permit_key,
            CrawlPermit.leased_until < now,
        )
    )
    occupied = set(
        session.scalars(
            select(CrawlPermit.slot).where(
                CrawlPermit.permit_key == permit_key,
                CrawlPermit.leased_until >= now,
            )
        )
    )
    slot = next((candidate for candidate in range(capacity) if candidate not in occupied), None)
    if slot is None:
        return None
    permit = CrawlPermit(
        permit_key=permit_key,
        slot=slot,
        policy_id=policy_id,
        holder_worker_id=worker_id,
        task_run_id=task_run_id,
        acquired_at=now,
        leased_until=now + timedelta(seconds=lease_seconds),
    )
    session.add(permit)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return None
    return permit


class CrawlCapacityLease(AbstractAsyncContextManager[None]):
    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        task_run_id: UUID,
        url: str,
        policy: CrawlPolicy | CrawlPolicySnapshot | None,
        progress_reporter: ProgressReporter | None,
        include_browser: bool,
        include_policy: bool,
    ) -> None:
        self._session_factory = session_factory
        self._task_run_id = task_run_id
        self._url = url
        self._policy = policy
        self._progress_reporter = progress_reporter
        self._include_browser = include_browser
        self._include_policy = include_policy
        self._permit_ids: list[UUID] = []
        self._stop = asyncio.Event()
        self._maintainer: asyncio.Task | None = None
        self._owner: asyncio.Task | None = None
        self._lost = False
        self._lease_token: UUID | None = None
        self._policy_label = policy.metric_slug if policy is not None else ""
        self._lease_seconds = _env_int("ATLAS_CRAWL_PERMIT_LEASE_SECONDS", 60)

    async def __aenter__(self) -> None:
        if not self._include_browser and (
            not self._include_policy or _policy_limit(self._policy) is None
        ):
            return None
        timeout = float(os.getenv("ATLAS_CRAWL_PERMIT_TIMEOUT_SECONDS", "120"))
        deadline = asyncio.get_running_loop().time() + max(1.0, timeout)
        waiting_emitted = False
        waiting_started_at: float | None = None
        listener = _PermitReleaseListener(self._session_factory)
        wait_metrics = capacity_metrics.CapacityWaitMetrics(policy=self._policy_label)
        metric_outcome = "lease_lost"
        try:
            while True:
                acquired: list[UUID] = []
                with self._session_factory() as session:
                    execution = current_task_execution()
                    lease = (
                        session.scalar(
                            select(TaskRunLease)
                            .where(
                                TaskRunLease.run_id == self._task_run_id,
                                TaskRunLease.lease_token == execution.lease_token,
                                TaskRunLease.expires_at > datetime.now(UTC),
                            )
                            .with_for_update()
                        )
                        if execution is not None
                        else None
                    )
                    run = session.get(TaskRun, self._task_run_id)
                    if (
                        run is None
                        or run.status != "running"
                        or lease is None
                        or run.cancellation_requested_at is not None
                        or lease.expires_at <= datetime.now(UTC)
                        or execution is None
                        or execution.run_id != self._task_run_id
                        or lease.lease_token != execution.lease_token
                    ):
                        raise RuntimeError("Task run lost ownership while waiting for crawl capacity.")
                    specs = []
                    if self._include_browser:
                        specs.append(
                            (
                                "browser:global",
                                _env_int("ATLAS_BROWSER_CONCURRENCY", 12),
                                None,
                            )
                        )
                    policy_limit = _policy_limit(self._policy)
                    if (
                        self._include_policy
                        and self._policy is not None
                        and policy_limit is not None
                    ):
                        policy_key = str(self._policy.id)
                        specs.append((f"policy:{policy_key}", policy_limit, self._policy.id))
                    for permit_key, capacity, policy_id in specs:
                        permit = _try_acquire(
                            session,
                            permit_key=permit_key,
                            capacity=capacity,
                            task_run_id=self._task_run_id,
                            worker_id=lease.worker_id,
                            policy_id=policy_id,
                            lease_seconds=self._lease_seconds,
                        )
                        if permit is None:
                            wait_metrics.blocked_by(
                                "browser" if permit_key == "browser:global" else "policy"
                            )
                            break
                        acquired.append(permit.id)
                    if len(acquired) == len(specs):
                        session.commit()
                        self._permit_ids = acquired
                        self._lease_token = execution.lease_token
                        break
                    if acquired:
                        session.execute(delete(CrawlPermit).where(CrawlPermit.id.in_(acquired)))
                    session.commit()
                if not waiting_emitted:
                    waiting_started_at = waiting_started_at or asyncio.get_running_loop().time()
                    if asyncio.get_running_loop().time() - waiting_started_at >= 0.5:
                        await emit_progress(
                            self._progress_reporter,
                            ProgressEvent(
                                resource=self._url,
                                phase="crawl_capacity",
                                status="waiting",
                                message="Waiting for crawl capacity.",
                            ),
                        )
                        waiting_emitted = True
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(f"Timed out waiting for crawl capacity for {self._url}.")
                await listener.wait(0.25)
            metric_outcome = "acquired"
        except asyncio.CancelledError:
            metric_outcome = "cancelled"
            raise
        except TimeoutError:
            metric_outcome = "timeout"
            raise
        finally:
            await listener.close()
            wait_metrics.finish(metric_outcome)
        if waiting_emitted:
            waited = asyncio.get_running_loop().time() - (waiting_started_at or deadline)
            await emit_progress(
                self._progress_reporter,
                ProgressEvent(
                    resource=self._url,
                    phase="crawl_capacity",
                    status="succeeded",
                    message="Crawl capacity acquired.",
                    duration=waited,
                ),
            )
        self._owner = asyncio.current_task()
        self._maintainer = asyncio.create_task(self._maintain())
        return None

    async def _maintain(self) -> None:
        interval = max(1.0, self._lease_seconds / 3)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            valid = await asyncio.to_thread(self._renew_permits)
            if not valid:
                self._lost = True
                if self._owner is not None:
                    self._owner.cancel()
                return

    def _renew_permits(self) -> bool:
        now = datetime.now(UTC)
        with self._session_factory() as session:
            lease = session.scalar(
                select(TaskRunLease)
                .where(
                    TaskRunLease.run_id == self._task_run_id,
                    TaskRunLease.lease_token == self._lease_token,
                    TaskRunLease.expires_at > now,
                )
                .with_for_update()
            )
            run = session.get(TaskRun, self._task_run_id)
            permits = list(
                session.scalars(
                    select(CrawlPermit).where(
                        CrawlPermit.id.in_(self._permit_ids),
                        CrawlPermit.task_run_id == self._task_run_id,
                        CrawlPermit.leased_until > now,
                    )
                )
            )
            if (
                lease is None
                or run is None
                or run.status != "running"
                or run.cancellation_requested_at is not None
                or len(permits) != len(self._permit_ids)
            ):
                session.rollback()
                return False
            leased_until = now + timedelta(seconds=self._lease_seconds)
            for permit in permits:
                permit.leased_until = leased_until
            session.commit()
            return True

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        if self._maintainer is not None:
            await asyncio.gather(self._maintainer, return_exceptions=True)
        if self._permit_ids:
            with self._session_factory() as session:
                session.execute(delete(CrawlPermit).where(CrawlPermit.id.in_(self._permit_ids)))
                session.execute(text(f"SELECT pg_notify('{_RELEASE_CHANNEL}', :payload)"), {"payload": str(self._task_run_id)})
                session.commit()
        if self._lost:
            capacity_metrics.lease_lost(scope="unknown", policy=self._policy_label)
            raise RuntimeError("Crawl capacity lease was lost.")


def capacity_lease(
    session: Session,
    *,
    task_run_id: UUID,
    url: str,
    policy: CrawlPolicy | CrawlPolicySnapshot | None,
    progress_reporter: ProgressReporter | None,
    include_browser: bool = True,
    include_policy: bool = True,
) -> CrawlCapacityLease:
    return CrawlCapacityLease(
        sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        task_run_id=task_run_id,
        url=url,
        policy=policy,
        progress_reporter=progress_reporter,
        include_browser=include_browser,
        include_policy=include_policy,
    )
