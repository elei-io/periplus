from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from periplus.crawl.api_runtime import ApiGraphRuntime, get_graph_runtime
from periplus.crawl.submission import submit_graph_run
from periplus.crawl.api.runs import GraphRunSubmission
from periplus.crawl.control.crawl_graphs.service import (
    CrawlGraphNotFoundError,
    CrawlGraphValidationError,
    get_graph,
)
from periplus.crawl.control.crawl_schedules.schemas import (
    CrawlScheduleCreate,
    CrawlScheduleList,
    CrawlScheduleRecord,
    CrawlScheduleResource,
    CrawlScheduleResourceList,
    CrawlScheduleUpdate,
    SchedulePreviewRequest,
    SchedulePreviewResponse,
)
from periplus.crawl.control.crawl_schedules.service import (
    CrawlScheduleConflictError,
    CrawlScheduleNotFoundError,
    CrawlScheduleValidationError,
    create_schedule,
    delete_schedule,
    get_schedule,
    get_schedule_resource,
    list_schedules,
    list_schedule_resources,
    preview_occurrences,
    record,
    set_schedule_enabled,
    update_schedule,
)
from periplus.platform.postgres.session import get_session


router = APIRouter(prefix="/crawl-plans", tags=["crawl-schedules"])
resource_router = APIRouter(prefix="/crawl-schedules", tags=["crawl-schedules"])


class ScheduleEnabledUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@resource_router.get("/", response_model=CrawlScheduleResourceList)
def list_resources(
    session: Annotated[Session, Depends(get_session)],
) -> CrawlScheduleResourceList:
    items = list_schedule_resources(session)
    return CrawlScheduleResourceList(items=items, total=len(items))


@resource_router.get("/{schedule_id}", response_model=CrawlScheduleResource)
def get_resource(
    schedule_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlScheduleResource:
    try:
        return get_schedule_resource(session, schedule_id)
    except CrawlScheduleNotFoundError as exc:
        raise _translate(exc) from exc


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, (CrawlGraphNotFoundError, CrawlScheduleNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CrawlScheduleConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get(
    "/{graph_id}/schedules",
    response_model=CrawlScheduleList,
)
def list_(
    graph_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlScheduleList:
    try:
        items = list_schedules(session, graph_id)
    except CrawlGraphNotFoundError as exc:
        raise _translate(exc) from exc
    return CrawlScheduleList(items=items, total=len(items))


@router.post(
    "/{graph_id}/schedules",
    response_model=CrawlScheduleRecord,
    status_code=201,
)
def create(
    graph_id: UUID,
    payload: CrawlScheduleCreate,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlScheduleRecord:
    try:
        return create_schedule(session, graph_id, payload)
    except (
        CrawlGraphNotFoundError,
        CrawlScheduleConflictError,
        CrawlScheduleValidationError,
    ) as exc:
        raise _translate(exc) from exc


@router.post(
    "/{graph_id}/schedules/preview",
    response_model=SchedulePreviewResponse,
)
def preview(
    graph_id: UUID,
    payload: SchedulePreviewRequest,
    session: Annotated[Session, Depends(get_session)],
) -> SchedulePreviewResponse:
    try:
        get_graph(session, graph_id)
        occurrences = preview_occurrences(payload)
    except (
        CrawlGraphNotFoundError,
        CrawlScheduleValidationError,
    ) as exc:
        raise _translate(exc) from exc
    return SchedulePreviewResponse(occurrences=occurrences)


@router.get(
    "/{graph_id}/schedules/{schedule_id}",
    response_model=CrawlScheduleRecord,
)
def get(
    graph_id: UUID,
    schedule_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlScheduleRecord:
    try:
        return record(get_schedule(session, graph_id, schedule_id))
    except CrawlScheduleNotFoundError as exc:
        raise _translate(exc) from exc


@router.put(
    "/{graph_id}/schedules/{schedule_id}",
    response_model=CrawlScheduleRecord,
)
def update(
    graph_id: UUID,
    schedule_id: UUID,
    payload: CrawlScheduleUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlScheduleRecord:
    try:
        return update_schedule(session, graph_id, schedule_id, payload)
    except (
        CrawlScheduleNotFoundError,
        CrawlScheduleConflictError,
        CrawlScheduleValidationError,
    ) as exc:
        raise _translate(exc) from exc


@router.put(
    "/{graph_id}/schedules/{schedule_id}/enabled",
    response_model=CrawlScheduleRecord,
)
def set_enabled(
    graph_id: UUID,
    schedule_id: UUID,
    payload: ScheduleEnabledUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlScheduleRecord:
    try:
        return set_schedule_enabled(session, graph_id, schedule_id, payload.enabled)
    except (
        CrawlScheduleNotFoundError,
        CrawlScheduleValidationError,
    ) as exc:
        raise _translate(exc) from exc


@router.delete(
    "/{graph_id}/schedules/{schedule_id}",
    status_code=204,
)
def delete(
    graph_id: UUID,
    schedule_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    try:
        delete_schedule(session, graph_id, schedule_id)
    except CrawlScheduleNotFoundError as exc:
        raise _translate(exc) from exc


@router.post(
    "/{graph_id}/schedules/{schedule_id}/run",
    response_model=GraphRunSubmission,
    status_code=202,
)
async def run_now(
    graph_id: UUID,
    schedule_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    runtime: Annotated[ApiGraphRuntime, Depends(get_graph_runtime)],
) -> GraphRunSubmission:
    try:
        schedule = get_schedule(session, graph_id, schedule_id)
        run = await submit_graph_run(
            session,
            runtime=runtime,
            graph_id=graph_id,
            urls=schedule.urls,
            trigger_kind="manual",
            trigger_schedule_id=schedule.id,
            max_crawls=schedule.max_crawls,
        )
    except (
        CrawlGraphNotFoundError,
        CrawlScheduleNotFoundError,
        CrawlGraphValidationError,
    ) as exc:
        raise _translate(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return GraphRunSubmission(plan_id=graph_id, run_id=run.id)
