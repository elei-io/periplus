"""Crawl-schedule validation, recurrence calculation, and persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterError, croniter
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from control.crawl_graphs.models import CrawlGraph
from control.crawl_graphs.service import get_graph
from control.urls import normalize_url

from .models import CrawlSchedule
from .schemas import (
    CrawlScheduleCreate,
    CrawlScheduleRecord,
    CrawlScheduleResource,
    CrawlScheduleUpdate,
    IntervalTiming,
    SchedulePreviewRequest,
    ScheduleStatus,
    ScheduleTiming,
    schedule_timing_adapter,
)


class CrawlScheduleNotFoundError(LookupError):
    pass


class CrawlScheduleConflictError(ValueError):
    pass


class CrawlScheduleValidationError(ValueError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _clean_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise CrawlScheduleValidationError("Schedule name must not be blank.")
    return cleaned


def validate_timing(timing: ScheduleTiming) -> None:
    if isinstance(timing, IntervalTiming):
        return
    try:
        timezone = ZoneInfo(timing.timezone)
    except ZoneInfoNotFoundError as exc:
        raise CrawlScheduleValidationError(
            f"Unknown schedule timezone: {timing.timezone}"
        ) from exc
    try:
        croniter(timing.expression, datetime.now(timezone)).get_next(datetime)
    except (CroniterError, ValueError, KeyError) as exc:
        raise CrawlScheduleValidationError(
            f"Invalid cron expression: {timing.expression}"
        ) from exc


def next_occurrence(
    timing: ScheduleTiming,
    *,
    after: datetime,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    first: bool = False,
) -> datetime | None:
    validate_timing(timing)
    after = _utc(after)
    start = _utc(starts_at) if starts_at is not None else None
    end = _utc(ends_at) if ends_at is not None else None
    if isinstance(timing, IntervalTiming):
        if first and start is not None and start > after:
            candidate = start
        else:
            anchor = start or after
            if first and start is None:
                candidate = after + timedelta(seconds=timing.seconds)
            else:
                elapsed = max(0.0, (after - anchor).total_seconds())
                steps = int(elapsed // timing.seconds) + 1
                candidate = anchor + timedelta(seconds=steps * timing.seconds)
    else:
        timezone = ZoneInfo(timing.timezone)
        base = max(after, start) if start is not None else after
        candidate = (
            croniter(timing.expression, base.astimezone(timezone))
            .get_next(datetime)
            .astimezone(UTC)
        )
    if start is not None and candidate < start:
        candidate = start
    if end is not None and candidate >= end:
        return None
    return candidate


def preview_occurrences(
    request: SchedulePreviewRequest,
    *,
    now: datetime | None = None,
) -> list[datetime]:
    cursor = _utc(now or datetime.now(UTC))
    values: list[datetime] = []
    for index in range(request.count):
        occurrence = next_occurrence(
            request.timing,
            after=cursor,
            starts_at=request.starts_at,
            ends_at=request.ends_at,
            first=index == 0,
        )
        if occurrence is None:
            break
        values.append(occurrence)
        cursor = occurrence
    return values


def _normalized_urls(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            url = normalize_url(value)
        except ValueError as exc:
            raise CrawlScheduleValidationError(
                f"Schedule URL must be absolute HTTP(S): {value}"
            ) from exc
        if url not in seen:
            seen.add(url)
            normalized.append(url)
    if not normalized:
        raise CrawlScheduleValidationError(
            "A crawl schedule requires at least one root URL."
        )
    return normalized


def _validate_crawl_budget(urls: list[str], max_crawls: int) -> None:
    if max_crawls < len(urls):
        raise CrawlScheduleValidationError(
            "Maximum crawls per run cannot be smaller than the number of root URLs."
        )


def schedule_status(
    schedule: CrawlSchedule, *, now: datetime | None = None
) -> ScheduleStatus:
    now = _utc(now or datetime.now(UTC))
    if not schedule.enabled:
        return "paused"
    if (
        schedule.maximum_run_count is not None
        and schedule.run_count >= schedule.maximum_run_count
    ):
        return "exhausted"
    if schedule.ends_at is not None and _utc(schedule.ends_at) <= now:
        return "ended"
    if schedule.starts_at is not None and _utc(schedule.starts_at) > now:
        return "not_started"
    return "active"


def record(
    schedule: CrawlSchedule, *, now: datetime | None = None
) -> CrawlScheduleRecord:
    return CrawlScheduleRecord(
        id=schedule.id,
        graph_id=schedule.graph_id,
        name=schedule.name,
        enabled=schedule.enabled,
        timing=schedule_timing_adapter.validate_python(schedule.timing),
        starts_at=schedule.starts_at,
        ends_at=schedule.ends_at,
        maximum_run_count=schedule.maximum_run_count,
        max_crawls=schedule.max_crawls,
        root_urls=schedule.root_urls,
        overlap_policy=schedule.overlap_policy,  # type: ignore[arg-type]
        misfire_policy=schedule.misfire_policy,  # type: ignore[arg-type]
        status=schedule_status(schedule, now=now),
        run_count=schedule.run_count,
        next_run_at=schedule.next_run_at,
        last_occurrence_at=schedule.last_occurrence_at,
        last_run_id=schedule.last_run_id,
        last_error=schedule.last_error,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def list_schedules(session: Session, graph_id: UUID) -> list[CrawlScheduleRecord]:
    get_graph(session, graph_id)
    schedules = session.scalars(
        select(CrawlSchedule)
        .where(CrawlSchedule.graph_id == graph_id)
        .order_by(CrawlSchedule.created_at)
    )
    return [record(schedule) for schedule in schedules]


def list_schedule_resources(session: Session) -> list[CrawlScheduleResource]:
    rows = session.execute(
        select(CrawlSchedule, CrawlGraph.slug)
        .join(CrawlGraph, CrawlGraph.id == CrawlSchedule.graph_id)
        .order_by(CrawlSchedule.created_at.desc())
    )
    return [
        CrawlScheduleResource(
            **record(schedule).model_dump(),
            graph_slug=graph_slug,
        )
        for schedule, graph_slug in rows
    ]


def get_schedule_resource(session: Session, schedule_id: UUID) -> CrawlScheduleResource:
    row = session.execute(
        select(CrawlSchedule, CrawlGraph.slug)
        .join(CrawlGraph, CrawlGraph.id == CrawlSchedule.graph_id)
        .where(CrawlSchedule.id == schedule_id)
    ).one_or_none()
    if row is None:
        raise CrawlScheduleNotFoundError(f"Crawl schedule {schedule_id} was not found.")
    schedule, graph_slug = row
    return CrawlScheduleResource(
        **record(schedule).model_dump(),
        graph_slug=graph_slug,
    )


def get_schedule(
    session: Session,
    graph_id: UUID,
    schedule_id: UUID,
    *,
    lock: bool = False,
) -> CrawlSchedule:
    statement = select(CrawlSchedule).where(
        CrawlSchedule.id == schedule_id,
        CrawlSchedule.graph_id == graph_id,
    )
    if lock:
        statement = statement.with_for_update()
    schedule = session.scalar(statement)
    if schedule is None:
        raise CrawlScheduleNotFoundError(f"Crawl schedule {schedule_id} was not found.")
    return schedule


def create_schedule(
    session: Session,
    graph_id: UUID,
    request: CrawlScheduleCreate,
    *,
    now: datetime | None = None,
) -> CrawlScheduleRecord:
    graph = get_graph(session, graph_id)
    if graph.root_node_id is None:
        raise CrawlScheduleValidationError(
            "A crawl schedule requires a graph root node."
        )
    validate_timing(request.timing)
    now = _utc(now or datetime.now(UTC))
    root_urls = _normalized_urls(request.root_urls)
    _validate_crawl_budget(root_urls, request.max_crawls)
    schedule = CrawlSchedule(
        graph_id=graph_id,
        name=_clean_name(request.name),
        enabled=request.enabled,
        timing=request.timing.model_dump(mode="json"),
        starts_at=request.starts_at,
        ends_at=request.ends_at,
        maximum_run_count=request.maximum_run_count,
        max_crawls=request.max_crawls,
        root_urls=root_urls,
        overlap_policy=request.overlap_policy,
        misfire_policy=request.misfire_policy,
        next_run_at=(
            next_occurrence(
                request.timing,
                after=now,
                starts_at=request.starts_at or now,
                ends_at=request.ends_at,
                first=True,
            )
            if request.enabled
            else None
        ),
        created_at=now,
        updated_at=now,
    )
    session.add(schedule)
    _flush(session)
    return record(schedule, now=now)


def update_schedule(
    session: Session,
    graph_id: UUID,
    schedule_id: UUID,
    request: CrawlScheduleUpdate,
    *,
    now: datetime | None = None,
) -> CrawlScheduleRecord:
    schedule = get_schedule(session, graph_id, schedule_id, lock=True)
    validate_timing(request.timing)
    now = _utc(now or datetime.now(UTC))
    root_urls = _normalized_urls(request.root_urls)
    _validate_crawl_budget(root_urls, request.max_crawls)
    schedule.name = _clean_name(request.name)
    schedule.enabled = request.enabled
    schedule.timing = request.timing.model_dump(mode="json")
    schedule.starts_at = request.starts_at
    schedule.ends_at = request.ends_at
    schedule.maximum_run_count = request.maximum_run_count
    schedule.max_crawls = request.max_crawls
    schedule.root_urls = root_urls
    schedule.overlap_policy = request.overlap_policy
    schedule.misfire_policy = request.misfire_policy
    schedule.next_run_at = (
        next_occurrence(
            request.timing,
            after=now,
            starts_at=request.starts_at or now,
            ends_at=request.ends_at,
            first=True,
        )
        if request.enabled
        and (
            request.maximum_run_count is None
            or schedule.run_count < request.maximum_run_count
        )
        else None
    )
    schedule.last_error = None
    schedule.updated_at = now
    _flush(session)
    return record(schedule, now=now)


def set_schedule_enabled(
    session: Session,
    graph_id: UUID,
    schedule_id: UUID,
    enabled: bool,
    *,
    now: datetime | None = None,
) -> CrawlScheduleRecord:
    schedule = get_schedule(session, graph_id, schedule_id, lock=True)
    now = _utc(now or datetime.now(UTC))
    schedule.enabled = enabled
    if enabled and (
        schedule.maximum_run_count is None
        or schedule.run_count < schedule.maximum_run_count
    ):
        timing = schedule_timing_adapter.validate_python(schedule.timing)
        schedule.next_run_at = next_occurrence(
            timing,
            after=now,
            starts_at=schedule.starts_at or schedule.created_at,
            ends_at=schedule.ends_at,
            first=True,
        )
    else:
        schedule.next_run_at = None
    schedule.updated_at = now
    session.flush()
    return record(schedule, now=now)


def delete_schedule(session: Session, graph_id: UUID, schedule_id: UUID) -> None:
    schedule = get_schedule(session, graph_id, schedule_id, lock=True)
    session.delete(schedule)
    session.flush()


def due_schedules(
    session: Session, *, now: datetime, limit: int = 100
) -> list[CrawlSchedule]:
    return list(
        session.scalars(
            select(CrawlSchedule)
            .where(
                CrawlSchedule.enabled.is_(True),
                CrawlSchedule.next_run_at.is_not(None),
                CrawlSchedule.next_run_at <= now,
            )
            .order_by(CrawlSchedule.next_run_at)
            .limit(limit)
        )
    )


def advance_occurrence(
    session: Session,
    schedule: CrawlSchedule,
    *,
    expected_occurrence: datetime,
    next_run_at: datetime | None,
    run_id: UUID | None = None,
    error: str | None = None,
    count_run: bool = False,
    now: datetime | None = None,
) -> bool:
    now = _utc(now or datetime.now(UTC))
    values: dict = {
        "next_run_at": next_run_at,
        "last_occurrence_at": expected_occurrence,
        "last_error": error,
        "updated_at": now,
    }
    if run_id is not None:
        values["last_run_id"] = run_id
    if count_run:
        values["run_count"] = CrawlSchedule.run_count + 1
    result = session.execute(
        update(CrawlSchedule)
        .where(
            CrawlSchedule.id == schedule.id,
            CrawlSchedule.next_run_at == expected_occurrence,
        )
        .values(**values)
    )
    session.flush()
    return result.rowcount == 1


def _flush(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise CrawlScheduleConflictError(
            "A schedule with this name already exists for the graph."
        ) from exc
