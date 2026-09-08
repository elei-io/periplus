import asyncio
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from periplus.operations.access.schemas import AccessEdit, AccessPolicy, AccessView, Capability
from periplus.operations.access.service import AccessStore

router = APIRouter(prefix="/access", tags=["Public access"])

@router.get("", response_model=AccessView)
async def read(request: Request):
    return await asyncio.to_thread(AccessStore(request.app.state.frontier_sessions).read)

@router.put("", response_model=AccessView)
async def save(value: AccessEdit, request: Request):
    if request.state.api_role != "admin":
        raise HTTPException(403, "Administrative access required")
    return await asyncio.to_thread(AccessStore(request.app.state.frontier_sessions).save,
        AccessPolicy.model_validate(value.model_dump(exclude={"expected_version"})), value.expected_version)

class Admission(BaseModel):
    consume: bool = True

@router.post("/admit/{capability}", status_code=204)
async def admit(capability: Capability, value: Admission, request: Request):
    if capability == "crawl":
        raise HTTPException(400, "Crawl admission happens when creating the execution")
    await asyncio.to_thread(AccessStore(request.app.state.frontier_sessions).admit, capability, consume=value.consume)
