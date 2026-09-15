"""Operator intent only; workers provision and execute rebuilds."""
import asyncio
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from periplus.materialization.rebuilds.control import BuildControl, RebuildConflict
from periplus.operations.access.service import AccessStore
from periplus.platform.postgres.session import SessionLocal
from periplus.query.binding import ExecutionContext, PublicationBinding
from periplus.operations.access.schemas import QueryLimits

router = APIRouter()


class RebuildRequest(BaseModel):
    page_size: int = Field(default=32, ge=1, le=128)


class BuildAction(BaseModel):
    action: Literal['pause', 'resume', 'retry', 'cancel', 'activate']


def _describe(control, build):
    result = {column.name: getattr(build, column.name) for column in build.__table__.columns}
    result['ranges'] = [{column.name: getattr(row, column.name) for column in row.__table__.columns}
                        for row in control.ranges(build.id)]
    return result


@router.get('/operations/materializations/runs')
async def list_runs():
    control = BuildControl()
    return await asyncio.to_thread(lambda: [_describe(control, build) for build in control.builds()])


@router.post('/operations/materializations/runs', status_code=202)
async def create_run(payload: RebuildRequest):
    try:
        identity = await asyncio.to_thread(BuildControl().create, payload.page_size)
        return {'id': identity}
    except RebuildConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/operations/materializations/runs/{identity}')
async def get_run(identity: UUID):
    control = BuildControl()
    try:
        return await asyncio.to_thread(lambda: _describe(control, control.get(identity)))
    except KeyError as exc:
        raise HTTPException(404, 'Build not found') from exc


@router.post('/operations/materializations/runs/{identity}/actions')
async def act(identity: UUID, payload: BuildAction):
    try:
        await asyncio.to_thread(BuildControl().action, identity, payload.action)
        return {'status': 'accepted'}
    except KeyError as exc:
        raise HTTPException(404, 'Build not found') from exc
    except RebuildConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/internal/query-context', response_model=ExecutionContext)
async def query_context(request: Request):
    if request.state.api_role != 'query':
        raise HTTPException(403, 'Query service credential required')
    def read():
        policy = AccessStore(SessionLocal).read().sql
        limits = QueryLimits(**{name: getattr(policy, name) for name in QueryLimits.model_fields})
        return ExecutionContext(limits=limits, publication=PublicationBinding(**BuildControl().binding()))
    return await asyncio.to_thread(read)
