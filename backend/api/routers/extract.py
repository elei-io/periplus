from fastapi import APIRouter

from domains.extract.models import ExtractOutput, Input
from domains.extract.service import extract as extract_service

router = APIRouter(prefix="/extract", tags=["extract"])


@router.post("/", response_model=ExtractOutput)
async def extract(request: Input) -> ExtractOutput:
    return await extract_service(**request.model_dump())
