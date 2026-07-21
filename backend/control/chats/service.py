from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from control.chats.models import Chat, ChatItem
from control.chats.schemas import (
    AssistantTurnContent,
    ChatItemContent,
    ChatItemRecord,
    ChatList,
    ChatRecord,
    ChatSummary,
    UserActionContent,
    UserMessageContent,
)


_content_adapter = TypeAdapter(ChatItemContent)
MAX_CONTEXT_ITEMS = 60
MAX_CONTEXT_CHARACTERS = 80_000


class ChatNotFoundError(ValueError):
    pass


class ChatItemNotFoundError(ValueError):
    pass


def create_chat(session: Session, title: str | None = None) -> ChatRecord:
    chat = Chat(title=(title or "New conversation").strip() or "New conversation")
    session.add(chat)
    session.flush()
    return _record(chat, [])


def list_chats(session: Session, *, limit: int = 30) -> ChatList:
    item_count = (
        select(func.count(ChatItem.id))
        .where(ChatItem.chat_id == Chat.id)
        .correlate(Chat)
        .scalar_subquery()
    )
    latest_content = (
        select(ChatItem.content)
        .where(ChatItem.chat_id == Chat.id)
        .order_by(ChatItem.sequence.desc())
        .limit(1)
        .correlate(Chat)
        .scalar_subquery()
    )
    rows = session.execute(
        select(Chat, item_count, latest_content)
        .order_by(Chat.updated_at.desc())
        .limit(limit)
    )
    total = session.scalar(select(func.count()).select_from(Chat)) or 0
    summaries: list[ChatSummary] = []
    for chat, count, content in rows:
        summaries.append(
            ChatSummary(
                id=chat.id,
                title=chat.title,
                preview=_preview(content) if content is not None else None,
                item_count=count,
                created_at=chat.created_at,
                updated_at=chat.updated_at,
            )
        )
    return ChatList(items=summaries, total=total)


def get_chat(session: Session, chat_id: UUID) -> ChatRecord:
    chat = session.scalar(
        select(Chat)
        .where(Chat.id == chat_id)
        .options(selectinload(Chat.items))
    )
    if chat is None:
        raise ChatNotFoundError(f"Chat {chat_id} was not found.")
    return _record(chat, chat.items)


def delete_chat(session: Session, chat_id: UUID) -> None:
    result = session.execute(delete(Chat).where(Chat.id == chat_id))
    if result.rowcount == 0:
        raise ChatNotFoundError(f"Chat {chat_id} was not found.")


def append_item(
    session: Session, chat_id: UUID, content: ChatItemContent
) -> ChatItemRecord:
    chat = session.scalar(
        select(Chat).where(Chat.id == chat_id).with_for_update()
    )
    if chat is None:
        raise ChatNotFoundError(f"Chat {chat_id} was not found.")
    validated = _content_adapter.validate_python(content)
    item = ChatItem(
        chat_id=chat.id,
        sequence=chat.next_sequence,
        content=validated.model_dump(mode="json"),
    )
    chat.next_sequence += 1
    chat.updated_at = datetime.now(UTC)
    if isinstance(validated, UserMessageContent) and chat.title == "New conversation":
        chat.title = _title(validated.text)
    session.add(item)
    session.flush()
    return _item_record(item)


def get_assistant_turn(
    session: Session, chat_id: UUID, item_id: UUID
) -> AssistantTurnContent:
    item = session.scalar(
        select(ChatItem).where(
            ChatItem.chat_id == chat_id,
            ChatItem.id == item_id,
        )
    )
    if item is None:
        raise ChatItemNotFoundError(f"Chat item {item_id} was not found.")
    content = _content_adapter.validate_python(item.content)
    if not isinstance(content, AssistantTurnContent):
        raise ChatItemNotFoundError(
            f"Chat item {item_id} is not an assistant turn."
        )
    return content


def build_agent_context(session: Session, chat_id: UUID) -> str | None:
    items = list(
        session.scalars(
            select(ChatItem)
            .where(ChatItem.chat_id == chat_id)
            .order_by(ChatItem.sequence.desc())
            .limit(MAX_CONTEXT_ITEMS)
        )
    )
    rendered: list[str] = []
    used = 0
    for item in items:
        segments = _context_segments(
            _content_adapter.validate_python(item.content)
        )
        for value in reversed(segments):
            if len(rendered) >= MAX_CONTEXT_ITEMS:
                break
            remaining = MAX_CONTEXT_CHARACTERS - used
            if remaining <= 0:
                break
            bounded = value if len(value) <= remaining else value[:remaining]
            rendered.append(bounded)
            used += len(bounded)
        if len(rendered) >= MAX_CONTEXT_ITEMS or used >= MAX_CONTEXT_CHARACTERS:
            break
    rendered.reverse()
    return "\n\n".join(rendered) or None


def _record(chat: Chat, items: list[ChatItem]) -> ChatRecord:
    return ChatRecord(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        items=[_item_record(item) for item in items],
    )


def _item_record(item: ChatItem) -> ChatItemRecord:
    return ChatItemRecord(
        id=item.id,
        sequence=item.sequence,
        content=_content_adapter.validate_python(item.content),
        created_at=item.created_at,
    )


def _title(text: str) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= 80 else f"{compact[:77]}…"


def _preview(content: dict[str, Any]) -> str | None:
    value = _content_adapter.validate_python(content)
    if isinstance(value, UserMessageContent):
        return value.text[:160]
    if isinstance(value, AssistantTurnContent):
        return value.summary[:160]
    if isinstance(value, UserActionContent):
        return value.label[:160]
    return None


def _context_segments(content: ChatItemContent) -> list[str]:
    if isinstance(content, UserMessageContent):
        return [f"USER:\n{content.text}"]
    if isinstance(content, UserActionContent):
        return [f"USER ACTION:\n{content.label}"]
    sections: list[str] = []
    for query in content.queries[-5:]:
        sections.append(
            "CATALOGUE QUERY:\n"
            f"SQL: {query.sql}\n"
            f"Columns: {json.dumps(query.columns)}\n"
            f"Rows ({query.row_count} returned, truncated={query.truncated}, first 10): "
            f"{json.dumps(query.rows[:10], default=str)}"
        )
    for tool in content.tools[-10:]:
        if tool.tool == "query_catalogue":
            continue
        detail = [
            f"TOOL {tool.tool}:",
            f"Arguments: {json.dumps(tool.arguments, default=str)}",
        ]
        if tool.search_results:
            detail.append(
                "Results: "
                + json.dumps(
                    [result.model_dump() for result in tool.search_results[:5]],
                    default=str,
                )
            )
        elif tool.result_preview:
            detail.append(f"Result: {tool.result_preview[:4_000]}")
        sections.append("\n".join(detail))
    for plan in content.acquisition_plans:
        sections.append("ACQUISITION PLAN:\n" + plan.model_dump_json())
    for change in content.schedule_changes:
        sections.append("SCHEDULE CHANGE PROPOSAL:\n" + change.model_dump_json())
    sections.append(f"ATLAS:\n{content.summary}")
    return sections
