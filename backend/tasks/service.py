import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from croniter import croniter
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer
from pydantic import ValidationError

from actions.extract.schemas import Input as ExtractInput
from actions.index.schemas import Input as IndexInput
from actions.crawl.schemas import Input as CrawlInput
from actions.calibrate.schemas import Input as CalibrateInput
from actions.shared.data_schema.schemas import Input as SchemaInput
from crawl_policies.service import snapshot_enabled_crawl_policies

from .models import Task, TaskRun, WorkerHeartbeat
from .schemas import (
    SearchInput,
    TaskCreate,
    TaskPrimitive,
    TaskRecord,
    TaskRunRecord,
    TaskRunSubmission,
    TaskRunTriggerKind,
    TaskOperationsRecord,
    TaskScheduleJson,
    TaskUpdate,
)


class TaskNotFoundError(Exception):
    pass


class TaskConflictError(Exception):
    pass


class TaskValidationError(Exception):
    pass


class TaskRunConflictError(Exception):
    pass


_INPUT_MODELS = {
    "search": SearchInput,
    "index": IndexInput,
    "crawl": CrawlInput,
    "schema": SchemaInput,
    "extract": ExtractInput,
    "calibrate": CalibrateInput,
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


def _json_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode()).hexdigest()


def _ad_hoc_identity_key(primitive: TaskPrimitive, input_json: dict) -> str:
    return f"adhoc:{primitive}:{_json_hash(input_json)}"


def _ad_hoc_task_name(primitive: TaskPrimitive, input_json: dict) -> str:
    if primitive in {"index", "schema", "extract", "calibrate"} and isinstance(input_json.get("url"), str):
        return f"Ad hoc {primitive}: {input_json['url']}"
    if primitive == "crawl" and isinstance(input_json.get("urls"), list) and input_json["urls"]:
        first_url = input_json["urls"][0]
        suffix = "" if len(input_json["urls"]) == 1 else f" +{len(input_json['urls']) - 1}"
        return f"Ad hoc crawl: {first_url}{suffix}"
    if primitive == "search" and isinstance(input_json.get("query"), str):
        return f"Ad hoc search: {input_json['query']}"
    return f"Ad hoc {primitive}"


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
        .options(defer(TaskRun.output_json))
        .where(TaskRun.task_id == task_id)
        .order_by(TaskRun.queued_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_run_record(run) for run in session.scalars(statement)]


def list_recent_task_runs(
    session: Session,
    primitive: TaskPrimitive,
    terminal_limit: int = 3,
) -> list[TaskRunRecord]:
    active = list(
        session.scalars(
            select(TaskRun)
            .options(defer(TaskRun.output_json))
            .where(TaskRun.primitive == primitive, TaskRun.status.in_(_ACTIVE_RUN_STATUSES))
            .order_by(TaskRun.queued_at.desc())
        )
    )
    terminal = list(
        session.scalars(
            select(TaskRun)
            .options(defer(TaskRun.output_json))
            .where(TaskRun.primitive == primitive, TaskRun.status.not_in(_ACTIVE_RUN_STATUSES))
            .order_by(TaskRun.finished_at.desc().nullslast(), TaskRun.queued_at.desc())
            .limit(terminal_limit)
        )
    )
    return [_run_record(run) for run in (*active, *terminal)]


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
        task_revision=task.revision,
        primitive=task.primitive,
        status="queued",
        trigger_kind=trigger_kind,
        queued_at=now,
        input_json=json.loads(json.dumps(task.input_json)),
        crawl_policy_snapshots_json=snapshot_enabled_crawl_policies(session),
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


def enqueue_ad_hoc_task_run(
    session: Session,
    primitive: TaskPrimitive,
    input_value: dict,
) -> TaskRunSubmission:
    input_json = _validate_input(primitive, input_value)
    identity_key = _ad_hoc_identity_key(primitive, input_json)
    task = session.scalar(select(Task).where(Task.identity_key == identity_key))

    if task is None:
        task = Task(
            name=_ad_hoc_task_name(primitive, input_json),
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

    active_statement = select(
        exists().where(
            TaskRun.task_id == task.id,
            TaskRun.status.in_(_ACTIVE_RUN_STATUSES),
        )
    )
    if session.scalar(active_statement):
        raise TaskRunConflictError("Task already has an active run.")

    now = datetime.now(UTC)
    run = TaskRun(
        task_id=task.id,
        task_revision=task.revision,
        primitive=task.primitive,
        status="queued",
        trigger_kind="manual",
        queued_at=now,
        input_json=json.loads(json.dumps(task.input_json)),
        crawl_policy_snapshots_json=snapshot_enabled_crawl_policies(session),
        warnings_json={},
    )
    try:
        with session.begin_nested():
            session.add(run)
            session.flush()
    except IntegrityError as exc:
        raise TaskRunConflictError("Task already has an active run.") from exc
    return TaskRunSubmission(task_id=task.id, run_id=run.id)


def get_task_run(session: Session, run_id: UUID) -> TaskRunRecord:
    run = session.get(TaskRun, run_id, options=(defer(TaskRun.output_json),))
    if run is None:
        raise TaskNotFoundError(f"Task run {run_id} was not found.")
    return _run_record(run)


def get_task_run_result(session: Session, run_id: UUID) -> object:
    run = session.get(TaskRun, run_id)
    if run is None:
        raise TaskNotFoundError(f"Task run {run_id} was not found.")
    if run.status in _ACTIVE_RUN_STATUSES:
        raise TaskRunConflictError("Task run has not finished.")
    if run.status != "succeeded":
        raise RuntimeError(run.error or f"Task run {run.status}.")

    return run.output_json or {}


def request_task_run_cancellation(session: Session, run_id: UUID) -> TaskRunRecord:
    run = session.get(TaskRun, run_id, options=(defer(TaskRun.output_json),))
    if run is None:
        raise TaskNotFoundError(f"Task run {run_id} was not found.")
    if run.status in {"succeeded", "failed", "cancelled", "skipped"}:
        return _run_record(run)

    now = datetime.now(UTC)
    run.cancellation_requested_at = now
    if run.status == "queued":
        run.status = "cancelled"
        run.cancelled_at = now
        run.finished_at = now
        run.error = "Task run cancelled before execution."
    session.flush()
    return _run_record(run)


def task_operations(session: Session, stale_after_seconds: int = 30) -> TaskOperationsRecord:
    now = datetime.now(UTC)
    workers = list(session.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.worker_id)))
    counts = dict(session.execute(select(TaskRun.status, func.count()).group_by(TaskRun.status)).all())
    oldest = session.scalar(select(func.min(TaskRun.queued_at)).where(TaskRun.status == "queued"))
    stale_before = now - timedelta(seconds=stale_after_seconds)
    recent = list(
        session.execute(
            select(TaskRun.queued_at, TaskRun.started_at, TaskRun.finished_at)
            .where(TaskRun.started_at.is_not(None), TaskRun.finished_at.is_not(None))
            .order_by(TaskRun.finished_at.desc())
            .limit(100)
        )
    )
    queue_latencies = [
        (run.started_at - run.queued_at).total_seconds()
        for run in recent
        if run.started_at is not None
    ]
    execution_times = [
        (run.finished_at - run.started_at).total_seconds()
        for run in recent
        if run.started_at is not None and run.finished_at is not None
    ]
    return TaskOperationsRecord(
        queued=counts.get("queued", 0),
        running=counts.get("running", 0),
        cancelling=session.scalar(
            select(func.count()).select_from(TaskRun).where(
                TaskRun.status == "running", TaskRun.cancellation_requested_at.is_not(None)
            )
        ) or 0,
        stale_workers=sum(not worker.stopping and worker.last_seen_at < stale_before for worker in workers),
        oldest_queued_at=oldest,
        average_queue_latency_seconds=(sum(queue_latencies) / len(queue_latencies) if queue_latencies else None),
        average_execution_seconds=(sum(execution_times) / len(execution_times) if execution_times else None),
        workers=workers,
    )
