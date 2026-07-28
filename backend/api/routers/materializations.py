"""Operations API for bounded fixed-projection maintenance."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from materialization.contracts import ProjectionName
from materialization.runtime import publish_run
from materialization.store import (
    AsyncMaterializationRunStore,
    MaterializationRun,
    RunMode,
)
from pydantic import BaseModel, ConfigDict, Field

from api.catalogue_control import CatalogueControl, get_catalogue_control

router = APIRouter(
    prefix="/operations/materializations",
    tags=["operations"],
)


class CreateMaterializationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RunMode
    stages: set[ProjectionName] = Field(min_length=1)
    item_budget: int = Field(default=100, ge=1, le=10_000)
    byte_budget: int = Field(
        default=64 * 1024 * 1024,
        ge=1,
        le=1024 * 1024 * 1024,
    )


class MaterializationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    mode: RunMode
    status: str
    requested_stages: tuple[ProjectionName, ...]
    stages: tuple[ProjectionName, ...]
    projector_versions: dict[str, int]
    source_snapshot: int
    catchup_snapshot: int
    catchup_target_snapshot: int | None
    catchup_stage: int
    catchup_cursors: dict[str, str | None]
    current_stage: int
    cursors: dict[str, str | None]
    destinations: dict[str, str]
    item_budget: int
    byte_budget: int
    source_items: int
    source_bytes: int
    output_rows: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None


@router.post("/runs", response_model=MaterializationRunResponse)
async def create_run(
    payload: CreateMaterializationRun,
    request: Request,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> MaterializationRun:
    source_snapshot = await control.latest_snapshot()
    if source_snapshot is None:
        raise HTTPException(
            status_code=409,
            detail="catalogue has no committed source snapshot",
        )
    store = _store(request)
    run = await store.create(
        mode=payload.mode,
        requested_stages=payload.stages,
        source_snapshot=source_snapshot,
        item_budget=payload.item_budget,
        byte_budget=payload.byte_budget,
    )
    try:
        await publish_run(request.app.state.graph_runtime.jetstream, run.id)
    except Exception:
        logging.exception(
            "materialization run %s is durable but awaiting publication",
            run.id,
        )
    return run


@router.get("/runs", response_model=list[MaterializationRunResponse])
async def list_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[MaterializationRun]:
    return await _store(request).list(limit=limit)


@router.get(
    "/runs/{run_id}",
    response_model=MaterializationRunResponse,
)
async def get_run(
    run_id: UUID,
    request: Request,
) -> MaterializationRun:
    run = await _store(request).get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail="materialization run was not found",
        )
    return run


def _store(request: Request) -> AsyncMaterializationRunStore:
    store = getattr(request.app.state, "materialization_runs", None)
    if not isinstance(store, AsyncMaterializationRunStore):
        raise RuntimeError("materialization run store is unavailable")
    return store
