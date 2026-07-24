"""Stateless catalogue analytics API."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from agents.catalogue_analytics import (
    AnalyticsQuestion,
    stream_catalogue_analysis,
)
from agents.catalogue_tools import CatalogueTools
from api.routers.catalogue import get_quack_runtime


router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post(
    "/questions/stream",
    response_class=EventSourceResponse,
)
async def question(
    payload: AnalyticsQuestion,
    request: Request,
) -> AsyncIterator[ServerSentEvent]:
    tools = CatalogueTools(get_quack_runtime(request))
    async for event in stream_catalogue_analysis(payload.question, tools):
        yield ServerSentEvent(data=event, event=event.type)
