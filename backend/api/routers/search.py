"""Durable Atlas chat and streaming agent-turn API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents.acquisition_tools import AcquisitionTools
from agents.catalogue_search import SearchEvent, stream_atlas_search
from agents.catalogue_tools import CatalogueTools
from api.catalogue_control import CatalogueControl, get_catalogue_control
from api.graph_submission import submit_graph_run
from api.routers.catalogue import get_quack_runtime
from config import get_optional
from control.chats.schemas import (
    AssistantTurnContent,
    ChatCreate,
    ChatItemRecord,
    ChatList,
    ChatQueryArtifact,
    ChatRecord,
    ChatRunRequest,
    ChatScheduleAction,
    ChatScheduleChangeAction,
    ChatToolArtifact,
    ChatTurnRequest,
    UserActionContent,
    UserMessageContent,
)
from control.chats.service import (
    ChatItemNotFoundError,
    ChatNotFoundError,
    append_item,
    build_agent_context,
    create_chat,
    delete_chat,
    get_assistant_turn,
    get_chat,
    list_chats,
)
from control.crawl_schedules.service import (
    CrawlScheduleNotFoundError,
    get_schedule,
    delete_schedule,
    set_schedule_enabled,
    update_schedule,
)
from db.session import get_session, session_scope


router = APIRouter(prefix="/chats", tags=["chats"])


class ChatGraphRunSubmission(BaseModel):
    graph_id: UUID
    run_id: UUID
    status: str = "queued"
    action_item: ChatItemRecord


@router.post("/", response_model=ChatRecord, status_code=201)
def create(
    payload: ChatCreate,
    session: Annotated[Session, Depends(get_session)],
) -> ChatRecord:
    return create_chat(session, payload.title)


@router.get("/", response_model=ChatList)
def list_(session: Annotated[Session, Depends(get_session)]) -> ChatList:
    return list_chats(session)


@router.get("/{chat_id}", response_model=ChatRecord)
def get_(
    chat_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> ChatRecord:
    try:
        return get_chat(session, chat_id)
    except ChatNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{chat_id}", status_code=204)
def delete_(
    chat_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    try:
        delete_chat(session, chat_id)
    except ChatNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/{chat_id}/turns/stream",
    response_class=EventSourceResponse,
)
async def turn(
    chat_id: UUID,
    payload: ChatTurnRequest,
    request: Request,
) -> AsyncIterator[ServerSentEvent]:
    try:
        with session_scope() as session:
            context = build_agent_context(session, chat_id)
            append_item(session, chat_id, UserMessageContent(text=payload.message))
    except ChatNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    catalogue_control = get_catalogue_control(request)
    catalogue_tools = CatalogueTools(
        get_quack_runtime(request),
        catalogue_control,
    )

    async def submit_page_crawl(graph_id: UUID, url: str) -> UUID:
        with session_scope() as session:
            run = await submit_graph_run(
                session,
                graph_id=graph_id,
                urls=[url],
                catalogue_snapshot_resolver=catalogue_control.latest_snapshot,
                trigger_kind="manual",
                max_crawls=1,
            )
        return run.id

    acquisition_tools = AcquisitionTools(
        catalogue_control,
        brave_api_key=get_optional("BRAVE_SEARCH_API_KEY"),
        page_crawl_submitter=submit_page_crawl,
    )
    collector = _TurnCollector()
    async for event in stream_atlas_search(
        payload.message,
        catalogue_tools,
        acquisition_tools,
        conversation_context=context,
    ):
        collector.apply(event)
        if event.type in {"run.completed", "run.failed"}:
            content = collector.content(event)
            with session_scope() as session:
                item = append_item(session, chat_id, content)
            event.chat_item_id = item.id
        yield ServerSentEvent(data=event, event=event.type)


@router.post(
    "/{chat_id}/items/{item_id}/run",
    response_model=ChatGraphRunSubmission,
    status_code=202,
)
async def run_plan(
    chat_id: UUID,
    item_id: UUID,
    payload: ChatRunRequest,
    session: Annotated[Session, Depends(get_session)],
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> ChatGraphRunSubmission:
    try:
        turn = get_assistant_turn(session, chat_id, item_id)
    except (ChatNotFoundError, ChatItemNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if payload.plan_index >= len(turn.acquisition_plans):
        raise HTTPException(status_code=422, detail="This chat turn has no acquisition plan.")
    plan = turn.acquisition_plans[payload.plan_index]
    if payload.max_crawls < len(plan.start_urls):
        raise HTTPException(
            status_code=422,
            detail="Maximum crawls must cover every acquisition start URL.",
        )
    try:
        run = await submit_graph_run(
            session,
            graph_id=plan.graph_id,
            urls=plan.start_urls,
            catalogue_snapshot_resolver=control.latest_snapshot,
            max_crawls=payload.max_crawls,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    action = append_item(
        session,
        chat_id,
        UserActionContent(
            action="graph_run_started",
            related_item_id=item_id,
            graph_id=plan.graph_id,
            graph_run_id=run.id,
            label=(
                f"Started graph run {run.id} from {plan.graph_slug} with "
                f"{len(plan.start_urls)} roots and a {payload.max_crawls}-page budget."
            ),
        ),
    )
    return ChatGraphRunSubmission(
        graph_id=plan.graph_id,
        run_id=run.id,
        action_item=action,
    )


@router.post(
    "/{chat_id}/items/{item_id}/schedule",
    response_model=ChatItemRecord,
    status_code=201,
)
def record_schedule(
    chat_id: UUID,
    item_id: UUID,
    payload: ChatScheduleAction,
    session: Annotated[Session, Depends(get_session)],
) -> ChatItemRecord:
    try:
        turn = get_assistant_turn(session, chat_id, item_id)
        if payload.plan_index >= len(turn.acquisition_plans):
            raise ChatItemNotFoundError("This chat turn has no acquisition plan.")
        plan = turn.acquisition_plans[payload.plan_index]
        schedule = get_schedule(session, plan.graph_id, payload.schedule_id)
    except (
        ChatNotFoundError,
        ChatItemNotFoundError,
        CrawlScheduleNotFoundError,
    ) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return append_item(
        session,
        chat_id,
        UserActionContent(
            action="schedule_created",
            related_item_id=item_id,
            graph_id=plan.graph_id,
            schedule_id=schedule.id,
            label=f"Created schedule {schedule.name!r} from the {plan.graph_slug} plan.",
        ),
    )


@router.post(
    "/{chat_id}/items/{item_id}/schedule-change",
    response_model=ChatItemRecord,
)
def apply_schedule_change(
    chat_id: UUID,
    item_id: UUID,
    payload: ChatScheduleChangeAction,
    session: Annotated[Session, Depends(get_session)],
) -> ChatItemRecord:
    try:
        turn = get_assistant_turn(session, chat_id, item_id)
        if payload.proposal_index >= len(turn.schedule_changes):
            raise ChatItemNotFoundError("This chat turn has no such schedule change.")
        proposal = turn.schedule_changes[payload.proposal_index]
        if proposal.action == "update":
            if proposal.replacement is None:
                raise ChatItemNotFoundError("Schedule update has no replacement definition.")
            update_schedule(
                session,
                proposal.graph_id,
                proposal.schedule_id,
                proposal.replacement,
            )
            action = "schedule_updated"
            verb = "Updated"
        elif proposal.action == "delete":
            delete_schedule(session, proposal.graph_id, proposal.schedule_id)
            action = "schedule_deleted"
            verb = "Deleted"
        else:
            enabled = proposal.action == "resume"
            set_schedule_enabled(
                session, proposal.graph_id, proposal.schedule_id, enabled
            )
            action = "schedule_resumed" if enabled else "schedule_paused"
            verb = "Resumed" if enabled else "Paused"
    except (ChatNotFoundError, ChatItemNotFoundError, CrawlScheduleNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return append_item(
        session,
        chat_id,
        UserActionContent(
            action=action,
            related_item_id=item_id,
            graph_id=proposal.graph_id,
            schedule_id=proposal.schedule_id,
            label=f"{verb} schedule {proposal.schedule_name!r}.",
        ),
    )


class _TurnCollector:
    def __init__(self) -> None:
        self.summary = ""
        self.tools: dict[str, dict[str, Any]] = {}
        self.tool_order: list[str] = []
        self.queries: dict[str, ChatQueryArtifact] = {}
        self.query_order: list[str] = []
        self.plans = []
        self.schedule_changes = []

    def apply(self, event: SearchEvent) -> None:
        call_id = event.call_id
        if event.type == "tool.started" and call_id and event.tool:
            if call_id not in self.tools:
                self.tool_order.append(call_id)
            self.tools[call_id] = {
                "call_id": call_id,
                "tool": event.tool,
                "arguments": event.arguments or {},
                "result_preview": None,
                "search_results": [],
                "status": "completed",
                "error": None,
            }
        elif event.type == "tool.completed" and call_id and call_id in self.tools:
            self.tools[call_id]["result_preview"] = event.result
        elif event.type == "search.completed" and call_id and call_id in self.tools:
            self.tools[call_id]["arguments"] = event.arguments or {}
            self.tools[call_id]["search_results"] = [
                result.model_copy(
                    update={
                        "description": (
                            result.description[:500]
                            if result.description is not None
                            else None
                        )
                    }
                )
                for result in (event.search_results or [])[:10]
            ]
        elif event.type == "query.started" and call_id and event.sql:
            if call_id not in self.query_order:
                self.query_order.append(call_id)
            self.queries[call_id] = ChatQueryArtifact(
                call_id=call_id,
                sql=event.sql,
                status="completed",
            )
        elif event.type == "query.completed" and call_id and event.sql:
            self.queries[call_id] = ChatQueryArtifact(
                call_id=call_id,
                query_id=event.query_id,
                sql=event.sql,
                columns=event.columns or [],
                column_types=event.column_types or [],
                rows=event.rows or [],
                row_count=event.row_count or 0,
                truncated=event.truncated or False,
                status="completed",
            )
        elif event.type == "query.failed" and call_id:
            query = self.queries.get(call_id)
            if query is not None:
                self.queries[call_id] = query.model_copy(
                    update={"status": "failed", "error": event.message}
                )
        elif event.type == "summary.delta" and event.delta:
            self.summary += event.delta
        elif event.type == "run.completed":
            self.summary = event.summary or self.summary
            self.plans = event.acquisition_plans or []
            self.schedule_changes = event.schedule_changes or []

    def content(self, event: SearchEvent) -> AssistantTurnContent:
        failed = event.type == "run.failed"
        summary = self.summary.strip()
        if failed:
            summary = event.message or "The Atlas agent could not complete this turn."
        return AssistantTurnContent(
            summary=summary,
            tools=[
                ChatToolArtifact.model_validate(self.tools[call_id])
                for call_id in self.tool_order
            ],
            queries=[self.queries[call_id] for call_id in self.query_order],
            acquisition_plans=self.plans,
            schedule_changes=self.schedule_changes,
            failed=failed,
        )
