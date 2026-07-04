from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import ValidationError

from actions.extract.schemas import Input as ExtractInput
from actions.index.schemas import Input as IndexInput
from actions.scrape.schemas import Input as ScrapeInput
from actions.shared.extract_schema.schemas import Input as SchemaInput

from .models import Task
from .schemas import SearchInput, TaskCreate, TaskPrimitive, TaskRecord, TaskScheduleJson, TaskUpdate


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


def create_task(session: Session, request: TaskCreate) -> TaskRecord:
    task = Task(
        name=request.name,
        primitive=request.primitive,
        input_json=_validate_input(request.primitive, request.input),
        schedule_json=_dump_schedule(request.schedule),
        dedupe_key=request.dedupe_key,
        enabled=request.enabled,
    )
    session.add(task)
    try:
        session.flush()
    except IntegrityError as exc:
        raise TaskConflictError("Task dedupe_key already exists.") from exc

    return _record(task)


def list_tasks(
    session: Session,
    primitive: TaskPrimitive | None = None,
    enabled: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TaskRecord]:
    statement = select(Task).order_by(Task.created_at.desc()).limit(limit).offset(offset)
    if primitive is not None:
        statement = statement.where(Task.primitive == primitive)
    if enabled is not None:
        statement = statement.where(Task.enabled == enabled)

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
    if "dedupe_key" in request.model_fields_set:
        task.dedupe_key = request.dedupe_key
    if "enabled" in patch:
        task.enabled = patch["enabled"]

    try:
        session.flush()
    except IntegrityError as exc:
        raise TaskConflictError("Task dedupe_key already exists.") from exc

    return _record(task)


def delete_task(session: Session, task_id: UUID) -> None:
    task = session.get(Task, task_id)
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} was not found.")

    session.delete(task)
    session.flush()
