"""Private execution history, never a public query contract."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

Source = Literal["public_console", "assistant", "sdk", "admin", "internal", "unknown"]
Operation = Literal["execute", "prepare"]
Outcome = Literal["success", "rejected", "failed", "timeout", "cancelled"]

class Execution(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    execution_id: UUID
    request_id: UUID | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime
    source: Source
    operation: Operation
    sql_text: str = Field(max_length=100_000)
    parameters: list[JsonValue] = Field(default_factory=list, max_length=100)
    query_template: str | None = Field(default=None, max_length=200_000)
    query_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    fingerprint_version: str = Field(max_length=80)
    relations: list[str] = Field(default_factory=list, max_length=2000)
    functions: list[str] = Field(default_factory=list, max_length=2000)
    features: dict[str, int] = Field(default_factory=dict)
    outcome: Outcome
    error_code: str | None = Field(default=None, max_length=80)
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    result_rows: int | None = Field(default=None, ge=0)
    result_bytes: int | None = Field(default=None, ge=0)
    truncated: bool | None = None
    source_snapshot: int | None = Field(default=None, ge=0)
    service_version: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def times(self):
        if self.finished_at < self.started_at:
            raise ValueError("finish must follow start")
        return self

class Stats(BaseModel):
    executions: int
    successes: int
    failures: int
    truncated: int
    p50_ms: float | None
    p95_ms: float | None
    total_ms: float

class Pattern(Stats):
    pattern_key: str
    query_template: str | None
    last_seen: datetime
    sources: list[str]

class Bucket(Stats):
    day: datetime

class Count(BaseModel):
    name: str
    count: int

class Dashboard(BaseModel):
    summary: Stats
    trend: list[Bucket]
    patterns: list[Pattern]
    pattern_count: int
    failures: list[Count]
    relations: list[Count]
    functions: list[Count]

class ExecutionSummary(BaseModel):
    execution_id: UUID
    started_at: datetime
    source: Source
    outcome: Outcome
    error_code: str | None
    elapsed_ms: float
    result_rows: int | None
    truncated: bool | None

class ExecutionPage(BaseModel):
    executions: list[ExecutionSummary]
    has_more: bool
