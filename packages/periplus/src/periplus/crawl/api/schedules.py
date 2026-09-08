import asyncio
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from periplus.crawl.control.schedules.schemas import DefinitionInput, DefinitionView, ScheduleInput, ScheduleView
from periplus.crawl.control.schedules.service import ScheduleStore, VersionConflict
from periplus.crawl.runtime.frontier_store import AdmissionDeferred

router = APIRouter(prefix="/request-definitions", tags=["Request schedules"])

async def call(request, method, *args, **kwargs):
    if request.state.api_role != "admin":
        raise HTTPException(403, "Request schedules require administrative access")
    store = ScheduleStore(request.app.state.frontier_sessions)
    try:
        return await asyncio.to_thread(getattr(store, method), *args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, "Definition or schedule not found") from exc
    except VersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except AdmissionDeferred as exc:
        raise HTTPException(429, "Request admission is at capacity") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

class DefinitionEdit(DefinitionInput):
    expected_version: int = Field(ge=1)

class ScheduleEdit(ScheduleInput):
    expected_version: int = Field(ge=1)

@router.get("", response_model=list[DefinitionView])
async def definitions(request: Request):
    return await call(request, "definitions")

@router.post("", response_model=DefinitionView, status_code=201)
async def create(value: DefinitionInput, request: Request):
    return await call(request, "save_definition", value)

@router.put("/{identity}", response_model=DefinitionView)
async def edit(identity: UUID, value: DefinitionEdit, request: Request):
    return await call(request, "save_definition", DefinitionInput.model_validate(value.model_dump(exclude={"expected_version"})), identity, value.expected_version)

@router.get("/schedules", response_model=list[ScheduleView])
async def schedules(request: Request):
    return await call(request, "schedules")

@router.post("/{identity}/run", response_model=dict[str, UUID], status_code=201)
async def run(identity: UUID, request: Request):
    return {"request_id": await call(request, "run_now", identity)}

@router.post("/{identity}/schedules", response_model=ScheduleView, status_code=201)
async def add_schedule(identity: UUID, value: ScheduleInput, request: Request):
    return await call(request, "save_schedule", identity, value)

@router.put("/{identity}/schedules/{schedule_id}", response_model=ScheduleView)
async def edit_schedule(identity: UUID, schedule_id: UUID, value: ScheduleEdit, request: Request):
    return await call(request, "save_schedule", identity, ScheduleInput.model_validate(value.model_dump(exclude={"expected_version"})), schedule_id, value.expected_version)
