"""Structured incompatibility diagnostics for materialization compilation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class IncompatibilityCode(StrEnum):
    """Stable reason codes exposed by the compiler boundary."""

    INVALID_QUERY = "invalid_query"
    UNSUPPORTED_REFRESH_STRATEGY = "unsupported_refresh_strategy"
    UNSUPPORTED_QUERY_SHAPE = "unsupported_query_shape"
    UNSUPPORTED_RELATION = "unsupported_relation"
    UNSUPPORTED_PROJECTION = "unsupported_projection"
    SOURCE_NOT_READ = "source_not_read"
    KEY_REQUIRED = "key_required"
    KEY_NOT_PRESERVED = "key_not_preserved"


@dataclass(frozen=True, slots=True)
class Incompatibility:
    """One actionable explanation of why a query cannot yet be compiled."""

    code: IncompatibilityCode
    message: str
    sql_fragment: str | None = None
    documentation_anchor: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-ready diagnostic without exposing exception internals."""

        return asdict(self)


class IncompatibleQueryError(ValueError):
    """Raised when Atlas cannot prove a bounded materialization plan."""

    def __init__(self, incompatibility: Incompatibility) -> None:
        self.incompatibility = incompatibility
        super().__init__(
            f"[{incompatibility.code}] {incompatibility.message}"
        )

    @property
    def code(self) -> IncompatibilityCode:
        return self.incompatibility.code

    def as_dict(self) -> dict[str, Any]:
        """Return the stable error payload intended for future API consumers."""

        return self.incompatibility.as_dict()
