"""Operator access to durable crawler controls; no dispatch occurs in handlers."""
import asyncio
from uuid import UUID
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from periplus.crawl.control.collections.frontier_controls import (
    ControlVersionConflict, FrontierControlView, ReplaceFrontierSettings,
)

from periplus.crawl.control.collections.history import HistoryUnavailable
from periplus.crawl.runtime.capture_feed import CapturePage, capture_page, decode_capture_cursor
from periplus.crawl.runtime.live import LiveView, current_activity
from periplus.crawl.runtime.frontier_items import AcquisitionView, acquisition_view, enrich_readiness

from periplus.crawl.control.collections.lineage import ObservationLineagePage

router = APIRouter(prefix="/frontier", tags=["Frontier"])


def _admin(request: Request) -> None:
    if request.state.api_role != "admin":
        raise HTTPException(403, "Crawler control requires administrative access.")


@router.get("/controls", response_model=FrontierControlView)
def controls(request: Request):
    _admin(request)
    return request.app.state.frontier.control_view()


@router.put("/controls", response_model=FrontierControlView)
def replace_controls(payload: ReplaceFrontierSettings, request: Request):
    _admin(request)
    try:
        # Authentication identifies a service role, never an end-user identity.
        return request.app.state.frontier.replace_controls(payload, actor="admin_service")
    except ControlVersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/items/{identity}", response_model=AcquisitionView)
async def item(identity: UUID, request: Request):
    workers = await request.app.state.crawler_presence.read()
    value = await asyncio.to_thread(acquisition_view, request.app.state.frontier_sessions, identity,
                             public_only=request.state.api_role != "admin", workers=workers)
    if value is None:
        raise HTTPException(404, "Current frontier item not found.")
    return (await enrich_readiness([value], request.app.state.collection_history,
        public_only=request.state.api_role != "admin"))[0]


@router.get("/live", response_model=LiveView)
async def live(request: Request, collection_id: UUID | None = None):
    workers = await request.app.state.crawler_presence.read()
    current = await asyncio.to_thread(current_activity, request.app.state.frontier_sessions, collection_id=collection_id)
    if current.queued == 1 and current.upcoming:
        candidate = await asyncio.to_thread(acquisition_view, request.app.state.frontier_sessions,
            current.upcoming[0].acquisition_id, public_only=True, workers=workers)
        if candidate is not None:
            current = current.model_copy(update={"next_start_estimate": candidate.next_start_estimate,
                "estimate_unavailable_reason": candidate.estimate_unavailable_reason})
    try:
        history = await request.app.state.collection_history.live(now=current.as_of)
    except HistoryUnavailable:
        history = None
    recent = {item.observation_id: item for item in current.recent}
    if history is not None:
        recent.update({item.observation_id: item for item in history.recent})
    return LiveView(current=current, history=history, workers=workers,
        history_unavailable_reason="catalogue_history_unavailable" if history is None else None,
        recent=sorted(recent.values(), key=lambda item: (item.completed_at, item.observation_id), reverse=True)[:5])


@router.get("/observations/{identity}/lineage", response_model=ObservationLineagePage)
async def observation_lineage(identity: UUID, request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Query(max_length=512)] = None):
    try:
        page = await request.app.state.collection_history.observation_lineage(identity,
            public_only=request.state.api_role != "admin", limit=limit, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(422, "Invalid observation lineage cursor or page limit.") from exc
    except HistoryUnavailable as exc:
        raise HTTPException(503, "Observation lineage is unavailable; retry later.", headers={"Retry-After": "5"}) from exc
    if page is None:
        raise HTTPException(404, "Observation not found.")
    return page


@router.get("/captures", response_model=CapturePage)
async def captures(request: Request, cursor: Annotated[str | None, Query(max_length=1024)] = None):
    try:
        anchor = decode_capture_cursor(cursor)
    except ValueError as exc:
        raise HTTPException(422, "Invalid capture cursor.") from exc
    return await asyncio.to_thread(capture_page, request.app.state.frontier_sessions, anchor)
