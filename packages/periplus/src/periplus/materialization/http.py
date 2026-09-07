"""Operations API for complete materialization rebuilds."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from periplus.materialization.runtime import publish_plan
from periplus.materialization.store import (
    AsyncMaterializationRunStore,
    MaterializationRun,
    MaterializationRunActive,
)
from periplus.platform.catalogue.control import CatalogueControl, get_catalogue_control
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, computed_field

router = APIRouter(prefix="/operations/materializations", tags=["operations"])


class CreateMaterializationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_size: int = Field(default=500, ge=1, le=10_000)


class MaterializationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    source_snapshot: int
    covered_snapshot: int
    activation_snapshot: int | None
    registry_digest: str
    batch_size: int
    total_batches: int
    completed_batches: int
    source_items: int
    source_bytes: int
    output_rows: int
    output_bytes: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None

    @computed_field
    @property
    def progress(self) -> float:
        if self.status == "completed":
            return 1.0
        if self.total_batches == 0:
            return 0.0
        return self.completed_batches / self.total_batches


@router.post("/runs", response_model=MaterializationRunResponse)
async def create_run(
    payload: CreateMaterializationRun,
    request: Request,
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> MaterializationRun:
    source_snapshot = await control.latest_snapshot()
    if source_snapshot is None:
        raise HTTPException(409, "catalogue has no committed source snapshot")
    store = _store(request)
    try:
        run = await store.create(
            source_snapshot=source_snapshot,
            batch_size=payload.batch_size,
        )
    except MaterializationRunActive as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        await publish_plan(
            request.app.state.jetstream,
            store,
            run.id,
        )
    except Exception:
        logging.exception("rebuild %s awaits recovery publication", run.id)
    return run


@router.get("/runs", response_model=list[MaterializationRunResponse])
async def list_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[MaterializationRun]:
    return await _store(request).list(limit=limit)


@router.get("/runs/{run_id}", response_model=MaterializationRunResponse)
async def get_run(run_id: UUID, request: Request) -> MaterializationRun:
    run = await _store(request).get(run_id)
    if run is None:
        raise HTTPException(404, "materialization rebuild was not found")
    return run


def _store(request: Request) -> AsyncMaterializationRunStore:
    store = getattr(request.app.state, "materialization_runs", None)
    if not isinstance(store, AsyncMaterializationRunStore):
        raise RuntimeError("materialization run store is unavailable")
    return store
