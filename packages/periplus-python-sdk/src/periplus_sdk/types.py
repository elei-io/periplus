"""Public query wire types; SQL types and JSON values are preserved."""
from typing import Literal

from pydantic import BaseModel, Field, JsonValue


class Diagnostic(BaseModel):
    severity: str
    code: str
    message: str


class PreparedQuery(BaseModel):
    query_mode: Literal["stable", "experimental"]
    compiler_version: str
    optimizations: list[str]
    schema_version: str
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


class HelperField(BaseModel):
    name: str
    description: str


class QueryHelper(BaseModel):
    name: str
    kind: str
    description: str
    parameters: list[HelperField]
    columns: list[HelperField]
    notes: list[str]
    examples: list[str]


class QueryHelpers(BaseModel):
    catalogue_version: str
    helpers: list[QueryHelper]
