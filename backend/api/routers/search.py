from typing import Annotated

from fastapi import APIRouter, Query

from domains.search.models import SearchResult
from domains.search.service import search as search_service

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/", response_model=list[SearchResult])
async def search(
    query: str,
    max_results: Annotated[int, Query(ge=1)] = 10,
) -> list[SearchResult]:
    return await search_service(query=query, max_results=max_results)
