"""Periodic crawl-schedule admission through the ordinary graph-run runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from uuid import UUID, uuid5

from sqlalchemy import select

from periplus.platform.config import get_float
from periplus.crawl.control.crawl_graphs.service import freeze_graph
from periplus.crawl.control.crawl_schedules.models import CrawlSchedule
from periplus.crawl.control.crawl_schedules.schemas import schedule_timing_adapter
from periplus.crawl.control.crawl_schedules.service import (
    advance_occurrence,
    due_schedules,
    get_schedule,
    next_occurrence,
)
from periplus.platform.postgres.session import SessionLocal
from periplus.crawl.runtime.graph_queue import (
    GraphRun,
    get_graph_run,
    list_graph_runs,
)
from periplus.crawl.runtime.graph_runs import create_graph_run, resolve_policy_snapshots


_SCHEDULE_RUN_NAMESPACE = UUID("9bd8a69b-a0a6-4ff0-bf4c-f4f30148bfa8")


def scheduled_run_id(schedule_id: UUID, occurrence_at: datetime) -> UUID:
    return uuid5(
        _SCHEDULE_RUN_NAMESPACE,
        f"{schedule_id}:{occurrence_at.astimezone(UTC).isoformat()}",
    )


def _next_after(
    schedule: CrawlSchedule,
    *,
    after: datetime,
) -> datetime | None:
    timing = schedule_timing_adapter.validate_python(schedule.timing)
    return next_occurrence(
        timing,
        after=after,
        starts_at=schedule.starts_at or schedule.created_at,
        ends_at=schedule.ends_at,
    )


def _active_schedule_run(runs: list[GraphRun], schedule_id: UUID) -> bool:
    return any(
        run.trigger_schedule_id == schedule_id and run.status in {"queued", "running"}
        for run in runs
    )


async def _process_due_schedule(
    schedule_id: UUID,
    expected_occurrence: datetime,
    *,
    runs,
    requests,
    progress,
    jetstream,
    now: datetime,
) -> None:
    run_id = scheduled_run_id(schedule_id, expected_occurrence)
    existing = await get_graph_run(runs, run_id)
    with SessionLocal() as session:
        schedule = get_schedule(
            session,
            graph_id=_schedule_graph_id(session, schedule_id),
            schedule_id=schedule_id,
        )
        if schedule.next_run_at != expected_occurrence or not schedule.enabled:
            return
        if existing is not None:
            if existing.trigger_schedule_id != schedule.id:
                raise RuntimeError(
                    f"Scheduled run identity {run_id} has invalid provenance."
                )
            next_run_at = _next_after(schedule, after=expected_occurrence)
            if (
                schedule.maximum_run_count is not None
                and schedule.run_count + 1 >= schedule.maximum_run_count
            ):
                next_run_at = None
            advance_occurrence(
                session,
                schedule,
                expected_occurrence=expected_occurrence,
                next_run_at=next_run_at,
                run_id=run_id,
                count_run=True,
                now=now,
            )
            session.commit()
            return

        if (
            schedule.maximum_run_count is not None
            and schedule.run_count >= schedule.maximum_run_count
        ) or (schedule.ends_at is not None and schedule.ends_at <= now):
            advance_occurrence(
                session,
                schedule,
                expected_occurrence=expected_occurrence,
                next_run_at=None,
                now=now,
            )
            session.commit()
            return

        missed = expected_occurrence < now - timedelta(
            seconds=get_float("PERIPLUS_SCHEDULE_MISFIRE_GRACE_SECONDS")
        )
        if missed and schedule.misfire_policy == "skip":
            advance_occurrence(
                session,
                schedule,
                expected_occurrence=expected_occurrence,
                next_run_at=_next_after(schedule, after=now),
                now=now,
            )
            session.commit()
            return

        current_runs = await list_graph_runs(runs)
        if schedule.overlap_policy == "skip" and _active_schedule_run(
            current_runs, schedule.id
        ):
            advance_occurrence(
                session,
                schedule,
                expected_occurrence=expected_occurrence,
                next_run_at=_next_after(schedule, after=now),
                now=now,
            )
            session.commit()
            return

        snapshot = freeze_graph(session, schedule.graph_id)
        urls = schedule.urls
        policies = resolve_policy_snapshots(session, urls)
        schedule_snapshot = schedule
        session.commit()

        try:
            await create_graph_run(
                runs=runs,
                requests=requests,
                progress=progress,
                jetstream=jetstream,
                snapshot=snapshot,
                urls=urls,
                policy_resolver=policies.__getitem__,
                trigger_kind="schedule",
                run_id=run_id,
                trigger_schedule_id=schedule.id,
                max_crawls=schedule.max_crawls,
                now=expected_occurrence,
            )
        except Exception as exc:
            if await get_graph_run(runs, run_id) is None:
                with SessionLocal() as failure_session:
                    current = get_schedule(
                        failure_session,
                        schedule_snapshot.graph_id,
                        schedule_snapshot.id,
                    )
                    advance_occurrence(
                        failure_session,
                        current,
                        expected_occurrence=expected_occurrence,
                        next_run_at=_next_after(
                            current,
                            after=now if missed else expected_occurrence,
                        ),
                        error=str(exc) or type(exc).__name__,
                        now=now,
                    )
                    failure_session.commit()
                raise

    with SessionLocal() as completion_session:
        current = get_schedule(
            completion_session,
            schedule_snapshot.graph_id,
            schedule_snapshot.id,
        )
        next_run_at = _next_after(
            current,
            after=now if missed else expected_occurrence,
        )
        if (
            current.maximum_run_count is not None
            and current.run_count + 1 >= current.maximum_run_count
        ):
            next_run_at = None
        advance_occurrence(
            completion_session,
            current,
            expected_occurrence=expected_occurrence,
            next_run_at=next_run_at,
            run_id=run_id,
            count_run=True,
            now=now,
        )
        completion_session.commit()


def _schedule_graph_id(session, schedule_id: UUID) -> UUID:
    graph_id = session.scalar(
        select(CrawlSchedule.graph_id).where(CrawlSchedule.id == schedule_id)
    )
    if graph_id is None:
        raise LookupError(f"Crawl schedule {schedule_id} was not found.")
    return graph_id


async def run_schedule_tick(
    *,
    runs,
    requests,
    progress,
    jetstream,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(UTC)
    with SessionLocal() as session:
        due = [
            (schedule.id, schedule.next_run_at)
            for schedule in due_schedules(session, now=now)
            if schedule.next_run_at is not None
        ]
    if not due:
        return 0
    processed = 0
    for schedule_id, occurrence_at in due:
        try:
            await _process_due_schedule(
                schedule_id,
                occurrence_at,
                runs=runs,
                requests=requests,
                progress=progress,
                jetstream=jetstream,
                now=now,
            )
        except Exception:
            logging.exception(
                "scheduled graph occurrence failed",
                extra={"schedule_id": str(schedule_id)},
            )
        processed += 1
    return processed


async def _run_schedule_loop(
    stop: asyncio.Event,
    *,
    runs,
    requests,
    progress,
    jetstream,
) -> None:
    interval = get_float("PERIPLUS_SCHEDULE_POLL_SECONDS")
    while not stop.is_set():
        try:
            await run_schedule_tick(
                runs=runs,
                requests=requests,
                progress=progress,
                jetstream=jetstream,
            )
        except Exception:
            logging.exception("crawl scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


async def run_scheduler(stop, *, runs, requests, progress, jetstream, leases):
    from periplus.crawl.runtime.coverage_requests import run_coverage_scheduler
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(_run_schedule_loop(stop, runs=runs, requests=requests, progress=progress, jetstream=jetstream))
        tasks.create_task(run_coverage_scheduler(stop, leases=leases, runs=runs, requests=requests, progress=progress, jetstream=jetstream))
