import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from api.routers.action_runs import run_action
from actions.shared.progress import CrawlProgressEvent
from actions.search.schemas import SearchResult
from db.session import get_session

router = APIRouter(prefix="/search", tags=["search"])
_DONE = object()
_SEARCH_ADAPTER = TypeAdapter(list[SearchResult])


def _sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _search_stream(
    session: Session,
    query: str,
    max_results: int,
) -> AsyncIterator[str]:
    queue: asyncio.Queue[CrawlProgressEvent | list[SearchResult] | Exception | object] = asyncio.Queue()

    async def progress_callback(event: CrawlProgressEvent) -> None:
        await queue.put(event)

    async def run_search() -> None:
        try:
            result = await run_action(
                session=session,
                primitive="search",
                input_value={"query": query, "max_results": max_results},
                response_adapter=_SEARCH_ADAPTER,
                progress_callback=progress_callback,
            )
            await queue.put(result)
        except Exception as exc:
            await queue.put(exc)
        finally:
            await queue.put(_DONE)

    task = asyncio.create_task(run_search())

    try:
        while True:
            item = await queue.get()

            if item is _DONE:
                yield _sse_event("done", {})
                break

            if isinstance(item, Exception):
                yield _sse_event("error", {"message": str(item)})
                continue

            if isinstance(item, list):
                yield _sse_event(
                    "result",
                    [result.model_dump(mode="json") for result in item],
                )
                continue

            yield _sse_event("progress", asdict(item))
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@router.get("/", response_model=list[SearchResult])
async def search(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    query: str,
    max_results: Annotated[int, Query(ge=1)] = 10,
) -> list[SearchResult] | StreamingResponse:
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(
            _search_stream(session=session, query=query, max_results=max_results),
            media_type="text/event-stream",
        )

    return await run_action(
        session=session,
        primitive="search",
        input_value={"query": query, "max_results": max_results},
        response_adapter=_SEARCH_ADAPTER,
    )
