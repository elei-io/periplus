"""Typed query API requests, results and execution modes."""

from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from periplus.platform.catalogue.public import PUBLIC_SCHEMA

COMPILER_VERSION = "public-query-v17"


class QueryMode(StrEnum):
    STABLE = "stable"
    EXPERIMENTAL = "experimental"


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str = Field(min_length=1, max_length=1_000_000)
    schema_version: Literal["public_v1", "experimental"] | None = None
    parameters: list[JsonValue] = Field(default_factory=list, max_length=100)


class Diagnostic(BaseModel):
    severity: str
    code: str
    message: str


class PreparedQuery(BaseModel):
    query_mode: QueryMode = QueryMode.STABLE
    compiler_version: str = COMPILER_VERSION + ":stable"
    optimizations: list[str] = Field(default_factory=list)
    schema_version: Literal["public_v1", "experimental"] = PUBLIC_SCHEMA
    query_id: str
    sql: str
    parameters: list[JsonValue]
    diagnostics: list[Diagnostic]
    plan: str


class QueryResult(PreparedQuery):
    columns: list[str]
    types: list[str]
    rows: list[list[JsonValue]]
    truncated: bool
    elapsed_ms: float
    source_snapshot: int = Field(ge=0)
    row_count: int = Field(ge=0)
    result_bytes: int = Field(ge=0)
    truncation_reason: Literal["max_rows", "max_result_bytes"] | None = None
