from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from nats.js.errors import NotFoundError

from periplus.ingestion.admin import (
    DeadLetterList,
    DeadLetterRecord,
    list_dead_letters,
    requeue_repository_dead_letter,
)

router = APIRouter(prefix="/operations/repository", tags=["operations"])


@router.get("/dead-letters", response_model=DeadLetterList)
async def dead_letters(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> DeadLetterList:
    return await list_dead_letters(request.app.state.jetstream, limit)


@router.post("/dead-letters/{sequence}/requeue", response_model=DeadLetterRecord)
async def requeue(sequence: int, request: Request) -> DeadLetterRecord:
    if sequence < 1:
        raise HTTPException(
            status_code=422, detail="sequence must be greater than zero"
        )
    try:
        return await requeue_repository_dead_letter(request.app.state.jetstream, request.app.state.ingestion_results, sequence)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="repository dead letter was not found"
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
