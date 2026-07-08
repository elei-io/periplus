import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from api.routers.action_runs import run_action
from actions.shared.progress import CrawlProgressEvent
from actions.scrape.schemas import Input, ScrapeOutput
from db.session import get_session

router = APIRouter(prefix="/scrape", tags=["scrape"])
_DONE = object()
_SCRAPE_ADAPTER = TypeAdapter(ScrapeOutput)


def _sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _scrape_stream(session: Session, request: Input) -> AsyncIterator[str]:
    queue: asyncio.Queue[CrawlProgressEvent | ScrapeOutput | Exception | object] = asyncio.Queue()

    async def progress_callback(event: CrawlProgressEvent) -> None:
        await queue.put(event)

    async def run_scrape() -> None:
        try:
            result = await run_action(
                session=session,
                primitive="scrape",
                input_value=request.model_dump(),
                response_adapter=_SCRAPE_ADAPTER,
                progress_callback=progress_callback,
            )
            await queue.put(result)
        except Exception as exc:
            await queue.put(exc)
        finally:
            await queue.put(_DONE)

    task = asyncio.create_task(run_scrape())

    try:
        while True:
            item = await queue.get()

            if item is _DONE:
                yield _sse_event("done", {})
                break

            if isinstance(item, Exception):
                yield _sse_event("error", {"message": str(item)})
                continue

            if isinstance(item, ScrapeOutput):
                yield _sse_event("result", item.model_dump(mode="json"))
                continue

            yield _sse_event("progress", asdict(item))
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@router.post("/", response_model=ScrapeOutput)
async def scrape(
    request: Input,
    http_request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> ScrapeOutput | StreamingResponse:
    if "text/event-stream" in http_request.headers.get("accept", ""):
        return StreamingResponse(
            _scrape_stream(session=session, request=request),
            media_type="text/event-stream",
        )

    return await run_action(
        session=session,
        primitive="scrape",
        input_value=request.model_dump(),
        response_adapter=_SCRAPE_ADAPTER,
    )
