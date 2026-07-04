from fastapi import APIRouter

from actions.extract.schemas import ExtractOutput, Input
from actions.extract.service import extract as extract_service

router = APIRouter(prefix="/extract", tags=["extract"])


@router.post("/", response_model=ExtractOutput)
async def extract(request: Input) -> ExtractOutput:
    return await extract_service(**request.model_dump())
