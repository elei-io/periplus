from fastapi import APIRouter

from actions.index.schemas import IndexLink, Input
from actions.index.service import index as index_service

router = APIRouter(prefix="/index", tags=["index"])


@router.post("/", response_model=list[IndexLink])
async def index(
    request: Input,
) -> list[IndexLink]:
    return await index_service(
        **request.model_dump(),
    )
