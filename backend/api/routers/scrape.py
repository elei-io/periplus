from fastapi import APIRouter

from actions.scrape.schemas import Input, ScrapeOutput
from actions.scrape.service import scrape as scrape_service

router = APIRouter(prefix="/scrape", tags=["scrape"])


@router.post("/", response_model=ScrapeOutput)
async def scrape(request: Input) -> ScrapeOutput:
    return await scrape_service(**request.model_dump())
