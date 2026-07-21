"""Streaming catalogue-agent API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, StringConstraints

from agents.acquisition_tools import AcquisitionTools
from agents.catalogue_search import stream_atlas_search
from agents.catalogue_tools import CatalogueTools
from api.catalogue_control import get_catalogue_control
from api.routers.catalogue import get_quack_runtime
from config import get_optional


router = APIRouter(prefix="/search", tags=["search"])
SearchQuestion = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000),
]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: SearchQuestion


@router.post("/stream", response_class=EventSourceResponse)
async def search(
    payload: SearchRequest,
    request: Request,
) -> AsyncIterator[ServerSentEvent]:
    catalogue_control = get_catalogue_control(request)
    catalogue_tools = CatalogueTools(
        get_quack_runtime(request),
        catalogue_control,
    )
    acquisition_tools = AcquisitionTools(
        catalogue_control,
        brave_api_key=get_optional("BRAVE_SEARCH_API_KEY"),
    )
    async for event in stream_atlas_search(
        payload.question,
        catalogue_tools,
        acquisition_tools,
    ):
        yield ServerSentEvent(data=event, event=event.type)
