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
from actions.extract.schemas import ExtractOutput, Input
from db.session import get_session

router = APIRouter(prefix="/extract", tags=["extract"])
_DONE = object()
_EXTRACT_ADAPTER = TypeAdapter(ExtractOutput)


def _sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _extract_stream(session: Session, request: Input) -> AsyncIterator[str]:
    queue: asyncio.Queue[CrawlProgressEvent | ExtractOutput | Exception | object] = asyncio.Queue()

    async def progress_callback(event: CrawlProgressEvent) -> None:
        await queue.put(event)

    async def run_extract() -> None:
        try:
            result = await run_action(
                session=session,
                primitive="extract",
                input_value=request.model_dump(),
                response_adapter=_EXTRACT_ADAPTER,
                progress_callback=progress_callback,
            )
            await queue.put(result)
        except Exception as exc:
            await queue.put(exc)
        finally:
            await queue.put(_DONE)

    task = asyncio.create_task(run_extract())

    try:
        while True:
            item = await queue.get()

            if item is _DONE:
                yield _sse_event("done", {})
                break

            if isinstance(item, Exception):
                yield _sse_event("error", {"message": str(item)})
                continue

            if isinstance(item, ExtractOutput):
                yield _sse_event("result", item.model_dump(mode="json"))
                continue

            yield _sse_event("progress", asdict(item))
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@router.post("/", response_model=ExtractOutput)
async def extract(
    request: Input,
    http_request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> ExtractOutput | StreamingResponse:
    if "text/event-stream" in http_request.headers.get("accept", ""):
        return StreamingResponse(
            _extract_stream(session=session, request=request),
            media_type="text/event-stream",
        )

    return await run_action(
        session=session,
        primitive="extract",
        input_value=request.model_dump(),
        response_adapter=_EXTRACT_ADAPTER,
    )
