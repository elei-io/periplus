import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from actions.calibrate.schemas import CalibrationOutput, Input
from actions.shared.progress import CrawlProgressEvent
from api.routers.action_runs import run_action
from crawl_policies.schemas import (
    CrawlPolicyListResponse,
    CrawlPolicyRecord,
    CrawlPolicyUpdateRequest,
)
from crawl_policies.service import (
    count_crawl_policies,
    delete_crawl_policy,
    get_crawl_policy,
    list_crawl_policies,
    match_for_policy,
    update_crawl_policy,
)
from db.session import get_session

router = APIRouter(prefix="/crawl-policies", tags=["crawl-policies"])
_DONE = object()
_CALIBRATE_ADAPTER = TypeAdapter(CalibrationOutput)


def _record(policy) -> CrawlPolicyRecord:
    return CrawlPolicyRecord(
        id=policy.id,
        url_match_id=policy.url_match_id,
        match=match_for_policy(policy),
        enabled=policy.enabled,
        config=policy.config or {},
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _calibrate_stream(session: Session, request: Input) -> AsyncIterator[str]:
    queue: asyncio.Queue[CrawlProgressEvent | CalibrationOutput | Exception | object] = asyncio.Queue()

    async def progress_callback(event: CrawlProgressEvent) -> None:
        await queue.put(event)

    async def run_calibrate() -> None:
        try:
            result = await run_action(
                session=session,
                primitive="calibrate",
                input_value=request.model_dump(),
                response_adapter=_CALIBRATE_ADAPTER,
                progress_callback=progress_callback,
            )
            await queue.put(result)
        except Exception as exc:
            await queue.put(exc)
        finally:
            await queue.put(_DONE)

    task = asyncio.create_task(run_calibrate())

    try:
        while True:
            item = await queue.get()

            if item is _DONE:
                yield _sse_event("done", {})
                break

            if isinstance(item, Exception):
                yield _sse_event("error", {"message": str(item)})
                continue

            if isinstance(item, CalibrationOutput):
                yield _sse_event("result", item.model_dump(mode="json"))
                continue

            yield _sse_event("progress", asdict(item))
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@router.post("/calibrate", response_model=CalibrationOutput)
async def calibrate(
    request: Input,
    http_request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> CalibrationOutput | StreamingResponse:
    if "text/event-stream" in http_request.headers.get("accept", ""):
        return StreamingResponse(
            _calibrate_stream(session=session, request=request),
            media_type="text/event-stream",
        )

    return await run_action(
        session=session,
        primitive="calibrate",
        input_value=request.model_dump(),
        response_adapter=_CALIBRATE_ADAPTER,
    )


@router.get("/", response_model=CrawlPolicyListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
    match_pattern: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    template: Annotated[str | None, Query()] = None,
    mode: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CrawlPolicyListResponse:
    return CrawlPolicyListResponse(
        items=list_crawl_policies(
            session=session,
            match_pattern=match_pattern,
            enabled=enabled,
            template=template,
            mode=mode,
            limit=limit,
            offset=offset,
        ),
        total=count_crawl_policies(
            session=session,
            match_pattern=match_pattern,
            enabled=enabled,
            template=template,
            mode=mode,
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/{policy_id}", response_model=CrawlPolicyRecord)
def get(
    policy_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlPolicyRecord:
    policy = get_crawl_policy(session=session, policy_id=policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")

    return _record(policy)


@router.patch("/{policy_id}", response_model=CrawlPolicyRecord)
def update(
    policy_id: UUID,
    request: CrawlPolicyUpdateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlPolicyRecord:
    policy = get_crawl_policy(session=session, policy_id=policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")

    updated = update_crawl_policy(
        session=session,
        policy=policy,
        enabled=request.enabled,
        match=request.match,
        config=request.config,
    )
    return _record(updated)


@router.delete("/{policy_id}", status_code=204)
def delete(
    policy_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    policy = get_crawl_policy(session=session, policy_id=policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")

    delete_crawl_policy(session=session, policy=policy)
