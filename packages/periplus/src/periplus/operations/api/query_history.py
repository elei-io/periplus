"""Narrow internal append endpoint and private operator reads."""
import asyncio
from typing import Annotated, Literal
from uuid import UUID
from starlette.responses import JSONResponse
from fastapi import APIRouter, HTTPException, Query, Request
from periplus.operations.query_history.schemas import Dashboard, Execution, ExecutionPage
from periplus.operations.query_history.store import QueryHistoryStore

class PrivateResponse(JSONResponse):
    def __init__(self, content, status_code=200, headers=None, media_type=None, background=None):
        super().__init__(content, status_code=status_code, headers=headers, media_type=media_type, background=background)
        self.headers['Cache-Control'] = 'no-store'

router = APIRouter(tags=['Query history'], default_response_class=PrivateResponse)
_append_slots = asyncio.Semaphore(8)

def store(request):
    return QueryHistoryStore(request.app.state.frontier_sessions)

@router.post('/internal/query-history', status_code=204)
async def append(value: Execution, request: Request):
    if request.state.api_role not in {'query', 'admin'}:
        raise HTTPException(403, 'Internal access required')
    if _append_slots.locked():
        raise HTTPException(503, "History recorder is busy")
    try:
        async with _append_slots:
            # Keep the slot until the database thread stops, even on disconnect.
            task = asyncio.create_task(asyncio.to_thread(store(request).record, value))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.gather(task, return_exceptions=True)
                raise
    except ValueError:
        raise HTTPException(422, 'Execution is outside the retention window') from None

SourceFilter = Literal['public', 'all', 'public_console', 'assistant', 'sdk', 'admin', 'internal', 'unknown']
OperationFilter = Literal['execute', 'prepare']
PatternFilter = Annotated[str | None, Query(pattern=r'^(?:[0-9a-f]{64}|unparsed)$')]

@router.get('/query-history', response_model=Dashboard)
async def dashboard(request: Request, days: Annotated[int, Query(ge=1, le=30)]=7,
    source: SourceFilter='public', operation: OperationFilter='execute', pattern: PatternFilter=None,
    sort: Literal['executions', 'p95', 'failures', 'total']='executions', offset: Annotated[int, Query(ge=0, le=100000)]=0):
    return await asyncio.to_thread(store(request).dashboard, days=days, source=source, operation=operation,
                                  pattern=pattern, sort=sort, offset=offset)

@router.get('/query-history/executions', response_model=ExecutionPage)
async def executions(request: Request, days: Annotated[int, Query(ge=1, le=30)]=7,
    source: SourceFilter='public', operation: OperationFilter='execute', pattern: PatternFilter=None,
    offset: Annotated[int, Query(ge=0, le=100000)]=0):
    return await asyncio.to_thread(store(request).executions, days=days, source=source,
                                  operation=operation, pattern=pattern, offset=offset)

@router.get('/query-history/executions/{identity}', response_model=Execution)
async def detail(identity: UUID, request: Request):
    result = await asyncio.to_thread(store(request).detail, identity)
    if result is None:
        raise HTTPException(404, 'Execution not found or expired')
    return result
