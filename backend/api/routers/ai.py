"""Stateless Atlas AI console API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from agents.catalogue_ai import AiRequest, stream_catalogue_assistance
from agents.catalogue_tools import CatalogueTools
from api.catalogue_control import CatalogueControl, get_catalogue_control
from api.routers.catalogue import (
    get_compiler_definitions,
    get_quack_runtime,
)
from atlas_sql import AtlasCompiler
from repository.catalogue.compiler_definitions import (
    CatalogueCompilerDefinitions,
)
from control.catalogue_scalar_macros.models import CatalogueScalarMacroDefinition
from control.catalogue_table_macros.models import CatalogueTableMacroDefinition
from sqlalchemy import select


router = APIRouter(prefix="/ai", tags=["ai"])


@router.post(
    "/stream",
    response_class=EventSourceResponse,
)
async def question(
    payload: AiRequest,
    request: Request,
    definitions: Annotated[
        CatalogueCompilerDefinitions,
        Depends(get_compiler_definitions),
    ],
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> AsyncIterator[ServerSentEvent]:
    macro_descriptions = await control.run(
        lambda session, _catalogue: {
            f"{definition.schema_name}.{definition.macro_name}": definition.description
            for model in (
                CatalogueScalarMacroDefinition,
                CatalogueTableMacroDefinition,
            )
            for definition in session.scalars(
                select(model).where(model.description.is_not(None))
            )
            if definition.description is not None
        }
    )
    tools = CatalogueTools(
        get_quack_runtime(request),
        compiler=AtlasCompiler.embedded(
            catalogue_revision=definitions.revision
        ),
        compiler_purpose=definitions.interactive_purpose(),
        macro_descriptions=macro_descriptions,
    )
    async for event in stream_catalogue_assistance(payload, tools):
        yield ServerSentEvent(
            data=event.model_dump(mode="json"),
            event=event.type,
        )
