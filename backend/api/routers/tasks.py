from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from db.session import get_session
from tasks.schemas import TaskCreate, TaskPrimitive, TaskRecord, TaskUpdate
from tasks.service import (
    TaskConflictError,
    TaskNotFoundError,
    TaskValidationError,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    update_task,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("/", response_model=TaskRecord, status_code=201)
def create(request: TaskCreate, session: Annotated[Session, Depends(get_session)]) -> TaskRecord:
    try:
        return create_task(session, request)
    except TaskValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/", response_model=list[TaskRecord])
def list_(
    session: Annotated[Session, Depends(get_session)],
    primitive: Annotated[TaskPrimitive | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TaskRecord]:
    return list_tasks(
        session=session,
        primitive=primitive,
        enabled=enabled,
        limit=limit,
        offset=offset,
    )


@router.get("/{task_id}", response_model=TaskRecord)
def get(task_id: UUID, session: Annotated[Session, Depends(get_session)]) -> TaskRecord:
    try:
        return get_task(session, task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{task_id}", response_model=TaskRecord)
def update(
    task_id: UUID,
    request: TaskUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> TaskRecord:
    try:
        return update_task(session, task_id, request)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{task_id}", status_code=204)
def delete(task_id: UUID, session: Annotated[Session, Depends(get_session)]) -> Response:
    try:
        delete_task(session, task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(status_code=204)
