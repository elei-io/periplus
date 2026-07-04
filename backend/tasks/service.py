from datetime import UTC, datetime, timedelta
from uuid import UUID

from croniter import croniter
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import ValidationError

from actions.extract.schemas import Input as ExtractInput
from actions.index.schemas import Input as IndexInput
from actions.scrape.schemas import Input as ScrapeInput
from actions.shared.extract_schema.schemas import Input as SchemaInput

from .models import Task, TaskRun
from .schemas import (
    SearchInput,
    TaskCreate,
    TaskOrigin,
    TaskPrimitive,
    TaskRecord,
    TaskRunRecord,
    TaskRunTriggerKind,
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
    "scrape": ScrapeInput,
    "schema": SchemaInput,
    "extract": ExtractInput,
}
_ACTIVE_RUN_STATUSES = ("queued", "running")


def _validate_input(primitive: TaskPrimitive, value: dict) -> dict:
    model = _INPUT_MODELS[primitive]
    try:
        return model.model_validate(value).model_dump(mode="json")
    except ValidationError as exc:
        raise TaskValidationError(f"Invalid input for primitive '{primitive}'.") from exc


def _dump_schedule(schedule: TaskScheduleJson | None) -> dict | None:
    if schedule is None:
        return None

    return schedule.model_dump(mode="json")


def _record(task: Task) -> TaskRecord:
    return TaskRecord.model_validate(task)


def _run_record(run: TaskRun) -> TaskRunRecord:
    return TaskRunRecord.model_validate(run)


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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

    if kind == "cron":
        next_at = croniter(schedule.get("expr") or "* * * * *", earliest).get_next(datetime)
        if next_at.tzinfo is None:
            next_at = next_at.replace(tzinfo=UTC)
        else:
            next_at = next_at.astimezone(UTC)
        if end_at is not None and next_at > end_at:
            return None
        return next_at

    return None


def _next_run_after_enqueue(schedule: dict | None, now: datetime | None = None) -> datetime | None:
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
        if next_at.tzinfo is None:
            next_at = next_at.replace(tzinfo=UTC)
        else:
            next_at = next_at.astimezone(UTC)
    else:
        return None

    if end_at is not None and next_at > end_at:
        return None
    return next_at


def create_task(session: Session, request: TaskCreate) -> TaskRecord:
    task = Task(
        name=request.name,
        primitive=request.primitive,
        input_json=_validate_input(request.primitive, request.input),
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
    origin: TaskOrigin | None = None,
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
    if origin == "human":
        statement = statement.where(Task.created_by_effect_run_id.is_(None))
    if origin == "effect":
        statement = statement.where(Task.created_by_effect_run_id.is_not(None))

    return [_record(task) for task in session.scalars(statement)]


def get_task(session: Session, task_id: UUID) -> TaskRecord:
    task = session.get(Task, task_id)
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} was not found.")

    return _record(task)


def update_task(session: Session, task_id: UUID, request: TaskUpdate) -> TaskRecord:
    task = session.get(Task, task_id)
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} was not found.")

    patch = request.model_dump(exclude_unset=True)
    primitive = patch.get("primitive", task.primitive)
    if "name" in patch:
        task.name = patch["name"]
    if "primitive" in patch:
        task.primitive = patch["primitive"]
    if "input" in patch:
        task.input_json = _validate_input(primitive, patch["input"])
    elif "primitive" in patch:
        task.input_json = _validate_input(primitive, task.input_json)
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


def list_task_runs(
    session: Session,
    task_id: UUID,
    limit: int = 100,
    offset: int = 0,
) -> list[TaskRunRecord]:
    if session.get(Task, task_id) is None:
        raise TaskNotFoundError(f"Task {task_id} was not found.")

    statement = (
        select(TaskRun)
        .where(TaskRun.task_id == task_id)
        .order_by(TaskRun.queued_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_run_record(run) for run in session.scalars(statement)]


def enqueue_task_run(
    session: Session,
    task: Task,
    trigger_kind: TaskRunTriggerKind,
    now: datetime | None = None,
) -> TaskRun | None:
    active_statement = select(
        exists().where(
            TaskRun.task_id == task.id,
            TaskRun.status.in_(_ACTIVE_RUN_STATUSES),
        )
    )
    if session.scalar(active_statement):
        return None

    now = now or datetime.now(UTC)
    run = TaskRun(
        task_id=task.id,
        status="queued",
        trigger_kind=trigger_kind,
        queued_at=now,
        input_json=task.input_json,
        warnings_json={},
    )
    session.add(run)
    task.next_run_at = _next_run_after_enqueue(task.schedule_json, now=now)
    return run


def enqueue_due_task_runs(session: Session, limit: int = 20) -> int:
    now = datetime.now(UTC)
    statement = (
        select(Task)
        .where(
            Task.archived_at.is_(None),
            Task.schedule_json.is_not(None),
            Task.next_run_at.is_not(None),
            Task.next_run_at <= now,
        )
        .order_by(Task.next_run_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )

    enqueued = 0
    for task in session.scalars(statement):
        if enqueue_task_run(session, task, trigger_kind="scheduled", now=now) is not None:
            enqueued += 1

    session.flush()
    return enqueued
