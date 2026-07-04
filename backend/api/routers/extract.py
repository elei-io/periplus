import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from actions.shared.progress import CrawlProgressEvent
from actions.extract.schemas import ExtractOutput, Input
from actions.extract.service import extract as extract_service

router = APIRouter(prefix="/extract", tags=["extract"])
_DONE = object()


def _sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _extract_stream(request: Input) -> AsyncIterator[str]:
    queue: asyncio.Queue[CrawlProgressEvent | ExtractOutput | Exception | object] = asyncio.Queue()

    async def progress_callback(event: CrawlProgressEvent) -> None:
        await queue.put(event)

    async def run_extract() -> None:
        try:
            result = await extract_service(
                **request.model_dump(),
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
) -> ExtractOutput | StreamingResponse:
    if "text/event-stream" in http_request.headers.get("accept", ""):
        return StreamingResponse(
            _extract_stream(request),
            media_type="text/event-stream",
        )

    return await extract_service(**request.model_dump())
