"""The small contract shared by query passes, the compiler and the benchmark runner."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import duckdb
from pydantic import JsonValue
from sqlglot import exp

DecisionStatus = Literal[
    "applied", "deferred", "not_applicable", "budget_exceeded", "contract_mismatch"
]


@dataclass(frozen=True)
class PassContext:
    statement: exp.Expression
    parameters: list[JsonValue] | dict[str, JsonValue]
    schema: str
    catalogue_alias: str
    # Preparation has no pass-level connection: it can inspect syntax, not data.
    connection: duckdb.DuckDBPyConnection | None = None


@dataclass(frozen=True)
class PassDecision:
    status: DecisionStatus
    reason: str
    message: str
    statement: exp.Query | None = None
    counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if (self.status == "applied") != (self.statement is not None):
            raise ValueError("Only an applied pass may supply a rewritten query")


@dataclass(frozen=True)
class OptimizationPass:
    name: str
    run: Callable[[PassContext], PassDecision]
