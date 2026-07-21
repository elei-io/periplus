from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents.acquisition_tools import (
    AcquisitionPlan,
    ScheduleChangeProposal,
    SeedSearchResult,
)


MAX_QUERY_ARTIFACT_CHARACTERS = 100_000
MAX_QUERY_CELL_CHARACTERS = 2_000


class ChatToolArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_preview: str | None = Field(default=None, max_length=8_000)
    search_results: list[SeedSearchResult] = Field(default_factory=list, max_length=10)
    status: Literal["completed", "failed"] = "completed"
    error: str | None = Field(default=None, max_length=2_000)


class ChatQueryArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    query_id: str | None = None
    sql: str = Field(max_length=100_000)
    columns: list[str] = Field(default_factory=list, max_length=500)
    column_types: list[str] = Field(default_factory=list, max_length=500)
    rows: list[list[Any]] = Field(default_factory=list, max_length=200)
    row_count: int = Field(default=0, ge=0)
    truncated: bool = False
    status: Literal["completed", "failed"] = "completed"
    error: str | None = Field(default=None, max_length=2_000)

    @field_validator("rows", mode="before")
    @classmethod
    def bound_rows(cls, rows: Any) -> list[list[Any]]:
        """Keep useful tabular evidence without persisting unbounded page text."""
        if not isinstance(rows, list):
            return []
        bounded: list[list[Any]] = []
        used = 0
        for raw_row in rows[:200]:
            if not isinstance(raw_row, (list, tuple)):
                continue
            cell_limit = min(
                MAX_QUERY_CELL_CHARACTERS,
                max(64, MAX_QUERY_ARTIFACT_CHARACTERS // max(len(raw_row), 1)),
            )
            row = [_bound_query_value(value, cell_limit) for value in raw_row]
            size = len(json.dumps(row, default=str, ensure_ascii=False))
            if used + size > MAX_QUERY_ARTIFACT_CHARACTERS:
                break
            bounded.append(row)
            used += size
        return bounded


def _bound_query_value(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else f"{value[: limit - 1]}…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    normalized = json.loads(json.dumps(value, default=str, ensure_ascii=False))
    rendered = json.dumps(normalized, ensure_ascii=False)
    if len(rendered) <= limit:
        return normalized
    return f"{rendered[: limit - 1]}…"


class UserMessageContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["user_message"] = "user_message"
    text: str = Field(min_length=1, max_length=20_000)


class AssistantTurnContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["assistant_turn"] = "assistant_turn"
    summary: str = Field(max_length=20_000)
    tools: list[ChatToolArtifact] = Field(default_factory=list, max_length=100)
    queries: list[ChatQueryArtifact] = Field(default_factory=list, max_length=50)
    acquisition_plans: list[AcquisitionPlan] = Field(default_factory=list, max_length=10)
    schedule_changes: list[ScheduleChangeProposal] = Field(default_factory=list, max_length=10)
    failed: bool = False


class UserActionContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["user_action"] = "user_action"
    action: Literal[
        "graph_run_started",
        "schedule_created",
        "schedule_paused",
        "schedule_resumed",
        "schedule_deleted",
        "schedule_updated",
    ]
    related_item_id: UUID
    graph_id: UUID
    graph_run_id: UUID | None = None
    schedule_id: UUID | None = None
    label: str = Field(max_length=500)


ChatItemContent = Annotated[
    UserMessageContent | AssistantTurnContent | UserActionContent,
    Field(discriminator="kind"),
]


class ChatItemRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sequence: int
    content: ChatItemContent
    created_at: datetime


class ChatRecord(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    items: list[ChatItemRecord]


class ChatSummary(BaseModel):
    id: UUID
    title: str
    preview: str | None
    item_count: int
    created_at: datetime
    updated_at: datetime


class ChatList(BaseModel):
    items: list[ChatSummary]
    total: int


class ChatCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=20_000)


class ChatRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_crawls: int = Field(ge=1, le=1_000_000)
    plan_index: int = Field(default=0, ge=0, le=9)


class ChatScheduleAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_id: UUID
    plan_index: int = Field(default=0, ge=0, le=9)


class ChatScheduleChangeAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_index: int = Field(ge=0, le=9)
