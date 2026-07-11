"""Postgres-backed task definitions."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from croniter import croniter
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from actions.calibrate.schemas import Input as CalibrateInput
from actions.crawl.schemas import Input as CrawlInput
from actions.extract.schemas import Input as ExtractInput
from actions.index.schemas import Input as IndexInput
from actions.shared.data_schema.schemas import Input as SchemaInput

from .models import Task
from .schemas import (
    SearchInput,
    TaskCreate,
    TaskPrimitive,
    TaskRecord,
    TaskScheduleJson,
    TaskUpdate,
)


class TaskNotFoundError(Exception):
    pass


class TaskConflictError(Exception):
    pass


class TaskValidationError(Exception):
    pass


_INPUT_MODELS = {
    "search": SearchInput,
    "index": IndexInput,
    "crawl": CrawlInput,
    "schema": SchemaInput,
    "extract": ExtractInput,
    "calibrate": CalibrateInput,
}


def validate_input(primitive: TaskPrimitive, value: dict) -> dict:
    try:
        return _INPUT_MODELS[primitive].model_validate(value).model_dump(mode="json")
    except ValidationError as exc:
        raise TaskValidationError(f"Invalid input for primitive '{primitive}'.") from exc


def _dump_schedule(schedule: TaskScheduleJson | None) -> dict | None:
    return schedule.model_dump(mode="json") if schedule is not None else None


def _record(task: Task) -> TaskRecord:
    return TaskRecord.model_validate(task)


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _next_run_at(schedule: dict | None, now: datetime | None = None) -> datetime | None:
    if schedule is None:
        return None
    now = now or datetime.now(UTC)
    kind = schedule.get("kind")
    if kind == "once":
        run_at = _parse_datetime(schedule.get("run_at"))
        return run_at if run_at and run_at >= now else now
    start_at = _parse_datetime(schedule.get("start_at"))
    end_at = _parse_datetime(schedule.get("end_at"))
    if end_at is not None and now > end_at:
        return None
    earliest = start_at if start_at and now < start_at else now
    if kind == "interval":
        return earliest
    if kind != "cron":
        return None
    next_at = croniter(schedule.get("expr") or "* * * * *", earliest).get_next(datetime)
    next_at = next_at.replace(tzinfo=UTC) if next_at.tzinfo is None else next_at.astimezone(UTC)
    return None if end_at is not None and next_at > end_at else next_at


def next_run_after_enqueue(schedule: dict | None, now: datetime | None = None) -> datetime | None:
    if schedule is None:
        return None
    now = now or datetime.now(UTC)
    kind = schedule.get("kind")
    if kind == "once":
        return None
    end_at = _parse_datetime(schedule.get("end_at"))
    if end_at is not None and now >= end_at:
        return None
    if kind == "interval":
        next_at = now + timedelta(seconds=max(1, int(schedule.get("every_seconds") or 1)))
    elif kind == "cron":
        next_at = croniter(schedule.get("expr") or "* * * * *", now).get_next(datetime)
        next_at = next_at.replace(tzinfo=UTC) if next_at.tzinfo is None else next_at.astimezone(UTC)
    else:
        return None
    return None if end_at is not None and next_at > end_at else next_at


def create_task(session: Session, request: TaskCreate) -> TaskRecord:
    task = Task(
        name=request.name,
        primitive=request.primitive,
        input_json=validate_input(request.primitive, request.input),
        schedule_json=_dump_schedule(request.schedule),
        identity_key=request.identity_key,
    )
    task.next_run_at = _next_run_at(task.schedule_json)
    session.add(task)
    try:
        session.flush()
    except IntegrityError as exc:
        raise TaskConflictError("Task identity_key already exists.") from exc
    return _record(task)


def list_tasks(
    session: Session,
    primitive: TaskPrimitive | None = None,
    archived: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TaskRecord]:
    statement = select(Task).order_by(Task.created_at.desc()).limit(limit).offset(offset)
    if primitive is not None:
        statement = statement.where(Task.primitive == primitive)
    if archived is True:
        statement = statement.where(Task.archived_at.is_not(None))
    if archived is False:
        statement = statement.where(Task.archived_at.is_(None))
    return [_record(task) for task in session.scalars(statement)]


def get_task(session: Session, task_id: UUID) -> TaskRecord:
    task = session.get(Task, task_id)
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} was not found.")
    return _record(task)


def update_task(session: Session, task_id: UUID, request: TaskUpdate) -> TaskRecord:
    task = session.scalar(
        select(Task)
        .where(Task.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} was not found.")
    patch = request.model_dump(exclude_unset=True)
    if patch:
        task.revision += 1
    primitive = patch.get("primitive", task.primitive)
    if "name" in patch:
        task.name = patch["name"]
    if "primitive" in patch:
        task.primitive = patch["primitive"]
    if "input" in patch:
        task.input_json = validate_input(primitive, patch["input"])
    elif "primitive" in patch:
        task.input_json = validate_input(primitive, task.input_json)
    if "schedule" in request.model_fields_set:
        task.schedule_json = _dump_schedule(request.schedule)
        task.next_run_at = _next_run_at(task.schedule_json)
    if "identity_key" in request.model_fields_set:
        task.identity_key = request.identity_key
    if "archived_at" in request.model_fields_set:
        task.archived_at = request.archived_at
        if request.archived_at is None:
            task.archived_reason = None
    if "archived_reason" in request.model_fields_set:
        task.archived_reason = request.archived_reason
    try:
        session.flush()
    except IntegrityError as exc:
        raise TaskConflictError("Task identity_key already exists.") from exc
    return _record(task)


def archive_task(session: Session, task_id: UUID, reason: str = "manual") -> None:
    task = session.get(Task, task_id)
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} was not found.")
    task.archived_at = datetime.now(UTC)
    task.archived_reason = reason
    task.next_run_at = None
    session.flush()


def copy_task(session: Session, task_id: UUID) -> TaskRecord:
    task = session.get(Task, task_id)
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} was not found.")
    copied = Task(
        name=f"Copy of {task.name}",
        primitive=task.primitive,
        input_json=task.input_json,
        schedule_json=task.schedule_json,
        identity_key=None,
        archived_at=datetime.now(UTC),
        archived_reason="copied_draft",
    )
    session.add(copied)
    session.flush()
    return _record(copied)


def get_or_create_ad_hoc_task(
    session: Session,
    primitive: TaskPrimitive,
    input_value: dict,
) -> Task:
    input_json = validate_input(primitive, input_value)
    digest = sha256(
        json.dumps(input_json, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    identity_key = f"adhoc:{primitive}:{digest}"
    task = session.scalar(select(Task).where(Task.identity_key == identity_key))
    if task is not None:
        return task
    if primitive in {"index", "schema", "extract", "calibrate"} and isinstance(input_json.get("url"), str):
        name = f"Ad hoc {primitive}: {input_json['url']}"
    elif primitive == "crawl" and isinstance(input_json.get("urls"), list) and input_json["urls"]:
        first = input_json["urls"][0]
        suffix = "" if len(input_json["urls"]) == 1 else f" +{len(input_json['urls']) - 1}"
        name = f"Ad hoc crawl: {first}{suffix}"
    elif primitive == "search" and isinstance(input_json.get("query"), str):
        name = f"Ad hoc search: {input_json['query']}"
    else:
        name = f"Ad hoc {primitive}"
    task = Task(
        name=name,
        primitive=primitive,
        input_json=input_json,
        schedule_json=None,
        identity_key=identity_key,
        next_run_at=None,
    )
    session.add(task)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        task = session.scalar(select(Task).where(Task.identity_key == identity_key))
        if task is None:
            raise
    return task
