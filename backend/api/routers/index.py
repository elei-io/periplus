from typing import Annotated

from fastapi import APIRouter, Query

from domains.index.models import IndexLink
from domains.index.service import index as index_service

router = APIRouter(prefix="/index", tags=["index"])


@router.get("/", response_model=list[IndexLink])
async def index(
    url: str,
    max_depth: Annotated[int, Query(ge=0)] = 3,
    dedupe: bool = False,
    concurrency: Annotated[int, Query(ge=1)] = 10,
    include_crawl: Annotated[list[str] | None, Query()] = None,
    exclude_crawl: Annotated[list[str] | None, Query()] = None,
    include_result: Annotated[list[str] | None, Query()] = None,
    exclude_result: Annotated[list[str] | None, Query()] = None,
) -> list[IndexLink]:
    return await index_service(
        url=url,
        max_depth=max_depth,
        dedupe=dedupe,
        concurrency=concurrency,
        include_crawl=include_crawl,
        exclude_crawl=exclude_crawl,
        include_result=include_result,
        exclude_result=exclude_result,
    )
