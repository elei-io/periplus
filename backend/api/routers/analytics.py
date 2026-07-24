"""Stateless catalogue analytics API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from agents.catalogue_analytics import (
    AnalyticsQuestion,
    stream_catalogue_analysis,
)
from agents.catalogue_tools import CatalogueTools
from api.routers.catalogue import (
    get_compiler_definitions,
    get_quack_runtime,
)
from atlas_sql import AtlasCompiler
from repository.catalogue.compiler_definitions import (
    CatalogueCompilerDefinitions,
)


router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post(
    "/questions/stream",
    response_class=EventSourceResponse,
)
async def question(
    payload: AnalyticsQuestion,
    request: Request,
    definitions: Annotated[
        CatalogueCompilerDefinitions,
        Depends(get_compiler_definitions),
    ],
) -> AsyncIterator[ServerSentEvent]:
    tools = CatalogueTools(
        get_quack_runtime(request),
        compiler=AtlasCompiler.embedded(
            catalogue_revision=definitions.revision
        ),
        compiler_purpose=definitions.interactive_purpose(),
    )
    async for event in stream_catalogue_analysis(payload.question, tools):
        yield ServerSentEvent(data=event, event=event.type)
