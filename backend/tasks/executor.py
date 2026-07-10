from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, defer

from actions.extract.service import extract
from actions.index.service import index
from actions.crawl.service import crawl
from actions.calibrate.service import calibrate
from actions.search.service import search
from actions.shared.data_schema.service import schema
from actions.shared.cache import CacheOptions, resolve_cache_policy
from actions.shared.progress import ProgressEvent, ProgressReporter
from observability import task_metrics
from actions.shared.nats_progress import ProgressPublisher
from catalogue import Catalogue, CatalogueService, catalogue_config_from_env
from crawl_policies.service import find_crawl_policy_snapshot_for_url

from .models import TaskRun, TaskRunLease, WorkerHeartbeat
from .context import TaskExecutionContext, task_execution_scope
from worker.logging import worker_log

class TaskRunCancelled(Exception):
    pass


class TaskRunLeaseLost(Exception):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _env_seconds(name: str, default: float, *, minimum: float = 1.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _worker_lease_seconds() -> float:
    return _env_seconds("ATLAS_WORKER_LEASE_SECONDS", 45)


def _task_run_timeout_seconds() -> float:
    return _env_seconds("ATLAS_TASK_RUN_TIMEOUT_SECONDS", 1800)


def _json_safe(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[no-any-return]
    return json.loads(json.dumps(value, default=str))


@dataclass
class PrimitiveExecution:
    output_json: dict
    response_json: object
    warnings: list[dict]


def _has_durable_run_usage(run_id: UUID) -> bool:
    with Catalogue(catalogue_config_from_env()) as catalogue:
        return CatalogueService(catalogue).has_run_usage(run_id)


def _run_can_have_durable_usage(run: TaskRun) -> bool:
    payload = getattr(run, "input_json", {})
    cache = CacheOptions.model_validate(payload.get("cache") or {})
    if cache.mode == "no_store":
        return False
    urls = payload.get("urls")
    if not isinstance(urls, list):
        url = payload.get("url")
        urls = [url] if isinstance(url, str) else []
    if not urls or run.primitive == "search":
        return True
    snapshots = getattr(run, "crawl_policy_snapshots_json", [])
    return any(
        resolve_cache_policy(
            crawl_policy_config=(
                policy.config
                if (policy := find_crawl_policy_snapshot_for_url(snapshots, url=url))
                else None
            ),
            request=cache,
        ).mode
        != "no_store"
        for url in urls
    )


async def _bounded_result(
    run: TaskRun,
    *,
    counts: dict[str, int],
) -> dict[str, object]:
    """Build the complete, deliberately small terminal run result."""

    stores_result = (
        False
        if not _run_can_have_durable_usage(run)
        else await asyncio.to_thread(_has_durable_run_usage, run.id)
    )
    return {
        "version": 1,
        "primitive": run.primitive,
        "status": "succeeded",
        "counts": counts,
        "catalogue": {"run_id": str(run.id)} if stores_result else None,
    }


async def _execute_primitive(
    session: Session,
    run: TaskRun,
    progress_reporter: ProgressReporter | None = None,
) -> PrimitiveExecution:
    primitive = run.primitive
    payload = run.input_json

    if primitive == "search":
        results = await search(
            **payload,
            progress_reporter=progress_reporter,
            session=session,
            task_run_id=run.id,
        )
        response_json = await _bounded_result(run, counts={"results": len(results)})
        return PrimitiveExecution(
            output_json=response_json,
            response_json=response_json,
            warnings=[],
        )

    if primitive == "index":
        output = await index(
            **payload,
            progress_reporter=progress_reporter,
            session=session,
            task_run_id=run.id,
        )
        response_json = await _bounded_result(
            run,
            counts={
                "links": output.result_links,
                "discovered_links": output.discovered_links,
                "pages": output.pages,
                "failed_pages": output.failed_pages,
            },
        )
        return PrimitiveExecution(
            output_json=response_json,
            response_json=response_json,
            warnings=[],
        )

    if primitive == "crawl":
        raise RuntimeError("crawl primitive requires task-run execution context")
    if primitive == "schema":
        output = await schema(
            **payload,
            progress_reporter=progress_reporter,
            session=session,
            task_run_id=run.id,
        )
        response_json = await _bounded_result(
            run,
            counts={
                "schemas": 1,
            },
        )
        return PrimitiveExecution(
            output_json=response_json,
            response_json=response_json,
            warnings=[],
        )

    if primitive == "extract":
        output = await extract(
            **payload,
            progress_reporter=progress_reporter,
            session=session,
            task_run_id=run.id,
        )
        warnings = [_json_safe(warning) for warning in output.warnings]
        response_json = await _bounded_result(
            run,
            counts={
                "records": len(output.results),
                "query_parameters": (
                    len(output.query_params.params) if output.query_params else 0
                ),
                "warnings": len(output.warnings),
            },
        )
        return PrimitiveExecution(
            output_json=response_json,
            response_json=response_json,
            warnings=warnings,
        )

    if primitive == "calibrate":
        output = await calibrate(
            **payload,
            progress_reporter=progress_reporter,
            session=session,
            task_run_id=run.id,
        )
        response_json = await _bounded_result(
            run,
            counts={
                "candidates": len(output.candidates),
                "successful_candidates": sum(
                    1 for candidate in output.candidates if candidate.success
                ),
            },
        )
        return PrimitiveExecution(
            output_json=response_json,
            response_json=response_json,
            warnings=[],
        )

    raise ValueError(f"Unsupported task primitive: {primitive}")


async def _execute_crawl_primitive(
    session: Session,
    run: TaskRun,
    progress_reporter: ProgressReporter | None = None,
) -> PrimitiveExecution:
    output = await crawl(
        **run.input_json,
        progress_reporter=progress_reporter,
        session=session,
        task_run_id=run.id,
        retain_pages=False,
        include_links=False,
    )
    warnings: list[dict] = []
    for page in output.pages:
        page_warnings = [_json_safe(warning) for warning in page.quality_warnings]
        warnings.extend(page_warnings)

    response_json = await _bounded_result(
        run,
        counts={
            "requested_urls": output.stats.requested_urls,
            "succeeded": output.stats.succeeded,
            "failed": output.stats.failed,
        },
    )
    return PrimitiveExecution(
        output_json=response_json,
        response_json=response_json,
        warnings=warnings,
    )


def claim_next_run(
    session: Session,
    worker_id: str,
    lease_seconds: float | None = None,
) -> TaskRun | None:
    now = _utc_now()
    lease_seconds = lease_seconds if lease_seconds is not None else _worker_lease_seconds()
    statement = (
        select(TaskRun)
        .where(TaskRun.status == "queued", (TaskRun.retry_at.is_(None)) | (TaskRun.retry_at <= now))
        .order_by(TaskRun.queued_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    run = session.scalars(statement).first()
    if run is None:
        return None

    run.status = "running"
    run.started_at = now
    run.attempt += 1
    run.lease = TaskRunLease(
        worker_id=worker_id,
        attempt=run.attempt,
        claimed_at=now,
        heartbeat_at=now,
        expires_at=now + timedelta(seconds=lease_seconds),
    )
    session.flush()
    return run


def _lock_owned_run(session: Session, run_id: UUID, lease_token: UUID | None) -> TaskRun:
    if lease_token is not None:
        lease = session.scalar(
            select(TaskRunLease)
            .where(
                TaskRunLease.run_id == run_id,
                TaskRunLease.lease_token == lease_token,
                TaskRunLease.expires_at > _utc_now(),
            )
            .with_for_update()
        )
        if lease is None:
            raise TaskRunLeaseLost(f"Task run {run_id} lost its fenced lease.")
    run = session.scalar(
        select(TaskRun)
        .where(TaskRun.id == run_id, TaskRun.status == "running")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise TaskRunLeaseLost(f"Task run {run_id} is no longer owned.")
    return run


async def execute_run(
    session: Session,
    run_id: UUID,
    progress_reporter: ProgressReporter | None = None,
    lease_token: UUID | None = None,
) -> object | None:
    run = session.get(TaskRun, run_id)
    if run is None:
        return None

    task = run.task
    output_json: dict | None = None
    response_json: object | None = None
    warnings: list[dict] = []

    try:
        scope = (
            task_execution_scope(
                TaskExecutionContext(run_id=run_id, attempt=run.attempt, lease_token=lease_token)
            )
            if lease_token is not None
            else nullcontext()
        )
        with scope:
            if run.primitive == "crawl":
                execution = await _execute_crawl_primitive(session, run, progress_reporter)
            else:
                execution = await _execute_primitive(session, run, progress_reporter)
        output_json = execution.output_json
        response_json = execution.response_json
        warnings = execution.warnings

        if progress_reporter is not None:
            await progress_reporter.emit(
                ProgressEvent(
                    operation_id=f"{run_id}:persist_result:{run.attempt}",
                    phase="persist_result",
                    status="started",
                    message="Saving the result.",
                )
            )

        run = _lock_owned_run(session, run_id, lease_token)
        task = run.task

        warnings_json = {
            "codes": sorted(
                {str(warning["code"]) for warning in warnings if warning.get("code")}
            ),
            "count": len(warnings),
            "path": None,
        }

        run.status = "succeeded"
        run.output_json = output_json
        run.warnings_json = warnings_json
        run.error = None
        task.last_run_at = _utc_now()
    except TaskRunLeaseLost:
        session.rollback()
        raise
    except TaskRunCancelled as exc:
        session.rollback()
        run = _lock_owned_run(session, run_id, lease_token)
        task = run.task
        run.status = "cancelled"
        run.error = str(exc)
        run.cancelled_at = _utc_now()
        run.warnings_json = {"codes": [], "count": 0, "path": None}
    except Exception as exc:
        error = str(exc)
        session.rollback()
        try:
            run = _lock_owned_run(session, run_id, lease_token)
        except TaskRunLeaseLost as lease_exc:
            raise lease_exc from exc
        task = run.task
        warnings_json = {
            "codes": [],
            "count": 0,
            "path": None,
        }
        run.status = "failed"
        run.error = error
        run.warnings_json = warnings_json
    finally:
        owns_lease = lease_token is None or run.status in {"succeeded", "failed", "cancelled"}
        if owns_lease:
            now = _utc_now()
            run.finished_at = now
            task.updated_at = now
            lease = session.get(TaskRunLease, run_id)
            if lease_token is None or (lease is not None and lease.lease_token == lease_token):
                if lease is not None:
                    session.delete(lease)
            session.flush()

    return response_json


def recover_expired_runs(session: Session) -> int:
    now = _utc_now()
    timeout_before = now - timedelta(seconds=_task_run_timeout_seconds())
    candidate_ids = list(
        session.scalars(
            select(TaskRun.id)
            .outerjoin(TaskRunLease, TaskRunLease.run_id == TaskRun.id)
            .where(
                TaskRun.status == "running",
                (TaskRunLease.run_id.is_(None))
                | (TaskRunLease.expires_at <= now)
                | (TaskRun.started_at <= timeout_before),
            )
        )
    )
    recovered = 0
    for run_id in candidate_ids:
        lease = session.scalar(
            select(TaskRunLease)
            .where(TaskRunLease.run_id == run_id)
            .with_for_update()
        )
        run = session.scalar(
            select(TaskRun)
            .where(TaskRun.id == run_id, TaskRun.status == "running")
            .with_for_update(skip_locked=True)
        )
        if run is None:
            continue
        timed_out = run.started_at is not None and run.started_at <= timeout_before
        if lease is not None and lease.expires_at > now and not timed_out:
            continue
        previous_worker = lease.worker_id if lease is not None else None
        if lease is not None:
            session.delete(lease)
        if run.cancellation_requested_at is not None:
            run.status = "cancelled"
            run.cancelled_at = now
            run.finished_at = now
            run.error = "Task run cancelled after its worker lease expired."
            worker_log(
                logging.INFO,
                "run.cancelled",
                run_id=str(run.id),
                task_id=str(run.task_id),
                previous_worker=previous_worker,
                reason="lease_expired_after_cancellation",
            )
            task_metrics.cancelled(phase="running")
        elif timed_out:
            run.status = "failed"
            run.finished_at = now
            run.error = f"Task run exceeded the {_task_run_timeout_seconds():g}-second execution timeout."
            worker_log(
                logging.ERROR,
                "run.failed",
                run_id=str(run.id),
                task_id=str(run.task_id),
                previous_worker=previous_worker,
                attempt=run.attempt,
                error=run.error,
            )
        else:
            run.failed_attempts += 1
        if run.status == "running" and run.failed_attempts < run.max_attempts:
            run.status = "queued"
            run.retry_at = now
            run.started_at = None
            worker_log(
                logging.WARNING,
                "run.requeued",
                run_id=str(run.id),
                task_id=str(run.task_id),
                previous_worker=previous_worker,
                attempt=run.attempt,
                failed_attempts=run.failed_attempts,
                max_attempts=run.max_attempts,
                reason="lease_expired",
            )
        elif run.status == "running":
            run.status = "failed"
            run.finished_at = now
            run.error = f"Worker lease expired after {run.failed_attempts} failed attempts."
            worker_log(
                logging.ERROR,
                "run.failed",
                run_id=str(run.id),
                task_id=str(run.task_id),
                previous_worker=previous_worker,
                attempt=run.attempt,
                failed_attempts=run.failed_attempts,
                error=run.error,
            )
        task_metrics.recovered(reason="execution_timeout" if timed_out else "lease_expired")
        task_primitive = run.primitive
        if task_primitive is not None:
            task_metrics.attempt_finished(
                primitive=task_primitive,
                outcome="cancelled" if run.status == "cancelled" else "failed",
            )
        if (
            task_primitive is not None
            and run.status in {"succeeded", "failed", "cancelled", "skipped"}
            and run.started_at is not None
            and run.finished_at is not None
        ):
            task_metrics.terminal_run(
                primitive=task_primitive,
                status=run.status,
                trigger_kind=run.trigger_kind,
                duration_seconds=max(0.0, (run.finished_at - run.started_at).total_seconds()),
            )
        recovered += 1
    session.flush()
    return recovered


def release_task_run(
    session: Session,
    run_id: UUID,
    *,
    lease_token: UUID | None = None,
    count_failure: bool = False,
) -> bool:
    now = _utc_now()
    lease = session.scalar(
        select(TaskRunLease)
        .where(
            TaskRunLease.run_id == run_id,
            *(
                (TaskRunLease.lease_token == lease_token,)
                if lease_token is not None
                else ()
            ),
        )
        .with_for_update()
    )
    if lease_token is not None and lease is None:
        return False
    run = session.scalar(
        select(TaskRun)
        .where(TaskRun.id == run_id, TaskRun.status == "running")
        .with_for_update()
    )
    if lease is not None:
        session.delete(lease)
    if run is None:
        session.flush()
        return False
    if run.cancellation_requested_at is not None:
        run.status = "cancelled"
        run.cancelled_at = now
        run.finished_at = now
        run.error = "Task run cancellation was requested."
        task_metrics.cancelled(phase="running")
        task_primitive = run.primitive
        if task_primitive is not None:
            task_metrics.attempt_finished(primitive=task_primitive, outcome="cancelled")
        if task_primitive is not None and run.started_at is not None:
            task_metrics.terminal_run(
                primitive=task_primitive,
                status=run.status,
                trigger_kind=run.trigger_kind,
                duration_seconds=max(0.0, (run.finished_at - run.started_at).total_seconds()),
            )
    else:
        if count_failure:
            run.failed_attempts += 1
        if count_failure and run.failed_attempts >= run.max_attempts:
            run.status = "failed"
            run.finished_at = now
            run.error = f"Worker process exited after {run.failed_attempts} failed attempts."
        else:
            run.status = "queued"
            run.retry_at = now
            run.started_at = None
            run.error = None
        if count_failure:
            task_metrics.recovered(reason="worker_exit")
            task_primitive = run.primitive
            if task_primitive is not None:
                task_metrics.attempt_finished(primitive=task_primitive, outcome="failed")
            if (
                task_primitive is not None
                and run.status == "failed"
                and run.started_at is not None
                and run.finished_at is not None
            ):
                task_metrics.terminal_run(
                    primitive=task_primitive,
                    status=run.status,
                    trigger_kind=run.trigger_kind,
                    duration_seconds=max(
                        0.0, (run.finished_at - run.started_at).total_seconds()
                    ),
                )
        else:
            task_primitive = run.primitive
            if task_primitive is not None:
                task_metrics.attempt_finished(primitive=task_primitive, outcome="interrupted")
    session.flush()
    return True


def release_worker_runs(session: Session, worker_id: str) -> int:
    leases = list(
        session.execute(
            select(TaskRunLease.run_id, TaskRunLease.lease_token)
            .where(TaskRunLease.worker_id == worker_id)
            .order_by(TaskRunLease.run_id)
        )
    )
    return sum(
        release_task_run(session, run_id, lease_token=lease_token)
        for run_id, lease_token in leases
    )


def record_worker_heartbeat(
    session: Session,
    worker_id: str,
    *,
    capacity: int = 1,
    stopping: bool = False,
) -> None:
    now = _utc_now()
    worker = session.get(WorkerHeartbeat, worker_id)
    if worker is None:
        worker = WorkerHeartbeat(worker_id=worker_id, started_at=now)
        session.add(worker)
    worker.last_seen_at = now
    worker.capacity = capacity
    worker.active_run_count = int(
        session.scalar(
            select(func.count()).select_from(TaskRun).where(
                TaskRun.status == "running", TaskRunLease.worker_id == worker_id
            )
            .join(TaskRunLease, TaskRunLease.run_id == TaskRun.id)
        )
        or 0
    )
    worker.stopping = stopping
    session.flush()


def _renew_lease(
    session_factory,
    worker_id: str,
    run_id: UUID,
    lease_token: UUID,
    *,
    maintain_worker_heartbeat: bool = True,
) -> None:
    with session_factory() as session:
        session.execute(text("SET LOCAL lock_timeout = '2s'"))
        lease = session.scalar(
            select(TaskRunLease).where(
                TaskRunLease.run_id == run_id,
                TaskRunLease.worker_id == worker_id,
                TaskRunLease.lease_token == lease_token,
                TaskRunLease.expires_at > _utc_now(),
            )
            .with_for_update()
        )
        run = session.get(TaskRun, run_id)
        if lease is None or run is None or run.status != "running":
            raise TaskRunLeaseLost(f"Worker {worker_id} lost lease for task run {run_id}.")
        if run.cancellation_requested_at is not None:
            raise TaskRunCancelled("Task run cancellation requested.")
        now = _utc_now()
        lease.heartbeat_at = now
        lease.expires_at = now + timedelta(seconds=_worker_lease_seconds())
        if maintain_worker_heartbeat:
            record_worker_heartbeat(session, worker_id, capacity=_worker_concurrency())
        session.commit()


async def _maintain_lease(
    session_factory,
    worker_id: str,
    run_id: UUID,
    lease_token: UUID,
    stop: asyncio.Event,
    *,
    maintain_worker_heartbeat: bool = True,
) -> None:
    interval = _env_seconds("ATLAS_WORKER_HEARTBEAT_SECONDS", 10)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except TimeoutError:
            pass
        await asyncio.to_thread(
            _renew_lease,
            session_factory,
            worker_id,
            run_id,
            lease_token,
            maintain_worker_heartbeat=maintain_worker_heartbeat,
        )


async def run_worker_once(
    session_factory,
    worker_id: str | None = None,
    claimed_callback: Callable[[UUID, UUID], None] | None = None,
    claimed_run_id: UUID | None = None,
    claimed_lease_token: UUID | None = None,
) -> bool:
    worker_id = worker_id or f"{os.uname().nodename}:{os.getpid()}"
    owns_worker_heartbeat = claimed_run_id is None
    with session_factory() as session:
        if claimed_run_id is None:
            recover_expired_runs(session)
            record_worker_heartbeat(session, worker_id, capacity=_worker_concurrency())
            run = claim_next_run(session=session, worker_id=worker_id)
            if run is None:
                session.commit()
                return False
        else:
            run = session.get(TaskRun, claimed_run_id)
            lease = session.get(TaskRunLease, claimed_run_id)
            if (
                run is None
                or lease is None
                or run.status != "running"
                or lease.worker_id != worker_id
                or lease.lease_token != claimed_lease_token
                or lease.expires_at <= _utc_now()
            ):
                raise TaskRunLeaseLost(f"Preclaimed task run {claimed_run_id} lost ownership.")
        run_id = run.id
        task_id = run.task_id
        primitive = run.primitive
        trigger_kind = run.trigger_kind
        attempt = run.attempt
        lease_token = run.lease.lease_token
        queued_at = run.queued_at
        if owns_worker_heartbeat:
            record_worker_heartbeat(session, worker_id, capacity=_worker_concurrency())
        session.commit()
    if claimed_callback is not None:
        claimed_callback(run_id, lease_token)

    worker_log(
        logging.INFO,
        "run.claimed",
        run_id=str(run_id),
        task_id=str(task_id),
        primitive=primitive,
        trigger_kind=trigger_kind,
        attempt=attempt,
        queue_ms=round((_utc_now() - queued_at).total_seconds() * 1000),
    )
    task_metrics.queue_wait(
        primitive=primitive,
        seconds=max(0.0, (_utc_now() - queued_at).total_seconds()),
    )

    lease_stop = asyncio.Event()
    lease_task = asyncio.create_task(
        _maintain_lease(
            session_factory,
            worker_id,
            run_id,
            lease_token,
            lease_stop,
            maintain_worker_heartbeat=owns_worker_heartbeat,
        )
    )
    async with ProgressPublisher(run_id, attempt=attempt) as publisher:
        async def check_cancelled() -> None:
            with session_factory() as check_session:
                current = check_session.get(TaskRun, run_id)
                if current is None or current.cancellation_requested_at is not None:
                    raise TaskRunCancelled("Task run cancellation requested.")

        progress_reporter = ProgressReporter(
            sink=publisher.progress,
            cancellation_check=check_cancelled,
        )
        await progress_reporter.emit(
            ProgressEvent(
                operation_id=f"{run_id}:queue:0",
                phase="queue",
                status="succeeded",
                message="Worker claimed the task.",
            )
        )
        await progress_reporter.emit(
            ProgressEvent(
                operation_id=f"{run_id}:task:{attempt}",
                phase="task",
                status="started",
                message=f"{primitive.replace('_', ' ').title()} started.",
                metadata={"attempt": attempt},
            )
        )

        async def execute_claimed_run() -> None:
            with session_factory() as session:
                await execute_run(
                    session=session,
                    run_id=run_id,
                    progress_reporter=progress_reporter,
                    lease_token=lease_token,
                )
                session.commit()
                run = session.get(TaskRun, run_id)
                if run is None:
                    raise RuntimeError("Task run disappeared during execution.")
                await progress_reporter.emit(
                    ProgressEvent(
                        operation_id=f"{run_id}:persist_result:{attempt}",
                        phase="persist_result",
                        status="succeeded",
                        message="Result saved.",
                    )
                )
                await progress_reporter.emit(
                    ProgressEvent(
                        operation_id=f"{run_id}:task:{attempt}",
                        phase="task",
                        status="succeeded" if run.status == "succeeded" else "failed",
                        message=(
                            f"{primitive.replace('_', ' ').title()} completed."
                            if run.status == "succeeded"
                            else f"{primitive.replace('_', ' ').title()} failed."
                        ),
                        error=run.error,
                    )
                )
                await publisher.publish(run.status, {"status": run.status, "error": run.error})
                duration_ms = (
                    round((run.finished_at - run.started_at).total_seconds() * 1000)
                    if run.finished_at is not None and run.started_at is not None
                    else None
                )
                fields = {
                    "run_id": str(run.id),
                    "task_id": str(run.task_id),
                    "primitive": run.primitive,
                    "trigger_kind": run.trigger_kind,
                    "attempt": run.attempt,
                    "duration_ms": duration_ms,
                    "warnings": (run.warnings_json or {}).get("count", 0),
                }
                if run.status == "failed":
                    worker_log(logging.ERROR, "run.failed", **fields, error=run.error)
                elif run.status == "cancelled":
                    worker_log(logging.INFO, "run.cancelled", **fields, error=run.error)
                else:
                    worker_log(logging.INFO, "run.completed", **fields, status=run.status)

        execution_task = asyncio.create_task(execute_claimed_run())
        try:
            done, _pending = await asyncio.wait(
                {execution_task, lease_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if lease_task in done:
                error = lease_task.exception()
                execution_task.cancel()
                await asyncio.gather(execution_task, return_exceptions=True)
                if isinstance(error, TaskRunCancelled):
                    with session_factory() as session:
                        run = session.get(TaskRun, run_id)
                        lease = session.get(TaskRunLease, run_id)
                        owns_lease = lease is not None and lease.lease_token == lease_token
                        if run is not None and run.status == "running" and owns_lease:
                            now = _utc_now()
                            run.status = "cancelled"
                            run.cancelled_at = now
                            run.finished_at = now
                            run.error = str(error)
                            session.delete(lease)
                        session.commit()
                    status = "cancelled"
                    await publisher.publish(status, {"status": status, "error": str(error)})
                    worker_log(
                        logging.INFO,
                        "run.cancelled",
                        run_id=str(run_id),
                        error=str(error),
                    )
                elif error is not None:
                    worker_log(
                        logging.ERROR,
                        "run.lease_fencing_failed",
                        run_id=str(run_id),
                        error=str(error),
                    )
                    raise error
            else:
                await execution_task
        finally:
            lease_stop.set()
            await asyncio.gather(lease_task, return_exceptions=True)
            if owns_worker_heartbeat:
                with session_factory() as session:
                    record_worker_heartbeat(session, worker_id, capacity=_worker_concurrency())
                    session.commit()

    with session_factory() as metrics_session:
        completed_run = metrics_session.get(
            TaskRun,
            run_id,
            options=(defer(TaskRun.output_json),),
        )
        if (
            completed_run is not None
            and completed_run.status in {"succeeded", "failed", "cancelled", "skipped"}
            and completed_run.started_at is not None
            and completed_run.finished_at is not None
        ):
            task_metrics.terminal_run(
                primitive=primitive,
                status=completed_run.status,
                trigger_kind=trigger_kind,
                duration_seconds=max(
                    0.0,
                    (completed_run.finished_at - completed_run.started_at).total_seconds(),
                ),
            )
            task_metrics.attempt_finished(
                primitive=primitive,
                outcome=completed_run.status,
            )

    return True


def run_worker_once_sync(
    session_factory,
    worker_id: str | None = None,
    claimed_callback: Callable[[UUID, UUID], None] | None = None,
    claimed_run_id: UUID | None = None,
    claimed_lease_token: UUID | None = None,
) -> bool:
    return asyncio.run(
        run_worker_once(
            session_factory=session_factory,
            worker_id=worker_id,
            claimed_callback=claimed_callback,
            claimed_run_id=claimed_run_id,
            claimed_lease_token=claimed_lease_token,
        )
    )


def _worker_concurrency() -> int:
    try:
        return max(1, int(os.getenv("ATLAS_WORKER_CONCURRENCY", "4")))
    except ValueError:
        return 4
