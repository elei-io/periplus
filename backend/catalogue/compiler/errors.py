"""Structured diagnostics for unavailable catalogue-query optimization."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class OptimizationCode(StrEnum):
    INVALID_QUERY = "invalid_query"
    UNSUPPORTED_PURPOSE = "unsupported_purpose"
    UNSUPPORTED_QUERY_SHAPE = "unsupported_query_shape"
    UNSUPPORTED_RELATION = "unsupported_relation"
    UNMANAGED_RELATION = "unmanaged_relation"
    UNBOUNDED_RELATION = "unbounded_relation"
    UNSUPPORTED_PROJECTION = "unsupported_projection"
    UNSUPPORTED_FUNCTION = "unsupported_function"
    NONDETERMINISTIC_FUNCTION = "nondeterministic_function"
    RESERVED_RELATION = "reserved_relation"
    SOURCE_NOT_READ = "source_not_read"
    KEY_REQUIRED = "key_required"
    KEY_NOT_PRESERVED = "key_not_preserved"


@dataclass(frozen=True, slots=True)
class OptimizationDiagnostic:
    """Why Atlas could not prove a semantics-preserving optimization."""

    code: OptimizationCode
    message: str
    sql_fragment: str | None = None
    documentation_anchor: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class QueryOptimizationUnavailable(RuntimeError):
    """Raised when callers should execute the original valid SQL."""

    def __init__(self, diagnostic: OptimizationDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(f"[{diagnostic.code}] {diagnostic.message}")

    @property
    def code(self) -> OptimizationCode:
        return self.diagnostic.code

    def as_dict(self) -> dict[str, Any]:
        return self.diagnostic.as_dict()
