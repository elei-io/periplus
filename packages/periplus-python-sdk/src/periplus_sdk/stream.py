"""Incremental query frames. EOF is never a successful completion marker."""
from __future__ import annotations

import json
from typing import Literal

import httpx
from pydantic import BaseModel, Field, JsonValue, ValidationError

from .errors import ApiError, ResponseError, TransportError
from .types import PreparedQuery

MEDIA_TYPE = "application/x-ndjson"


class StreamResult(PreparedQuery):
    columns: list[str]
    types: list[str]
    source_snapshot: int = Field(ge=0)
    limits: dict[str, int]
    complete: bool = False
    truncated: bool = False
    truncation_reason: Literal["max_rows", "max_result_bytes"] | None = None
    row_count: int = 0
    result_bytes: int = 0
    elapsed_ms: float = 0


class Rows(BaseModel):
    type: Literal["rows"]
    rows: list[list[JsonValue]]


class Completion(BaseModel):
    type: Literal["complete"]
    row_count: int = Field(ge=0)
    result_bytes: int = Field(ge=0)
    truncated: bool
    truncation_reason: Literal["max_rows", "max_result_bytes"] | None
    elapsed_ms: float = Field(ge=0)


class QueryStream:
    """One response, consumed a batch at a time. Close early to cancel delivery."""

    def __init__(self, response: httpx.Response, *, allow_partial: bool = False):
        self.response = response
        self.allow_partial = allow_partial
        self.closed = False
        self._count = 0
        self._lines = response.iter_lines()
        try:
            if response.headers.get("content-type", "").split(";")[0] != MEDIA_TYPE:
                raise ResponseError("Expected a streaming query response.")
            frame = self._frame()
            if frame.get("type") != "metadata":
                raise ResponseError("Query stream is missing metadata.")
            self.result = StreamResult.model_validate(frame)
            if len(self.result.columns) != len(self.result.types):
                raise ResponseError("Query columns and types have inconsistent widths.")
        except ValidationError:
            self.close()
            raise ResponseError("Invalid query stream metadata.") from None
        except BaseException:
            self.close()
            raise

    def _frame(self):
        try:
            for line in self._lines:
                if not line.strip():
                    continue
                frame = json.loads(line)
                if not isinstance(frame, dict):
                    raise ResponseError("Invalid query stream frame.")
                if frame.get("type") == "error":
                    raise ApiError(frame.get("detail", "Query stream failed."),
                                   status_code=frame.get("status", 500), code=frame.get("code"))
                return frame
        except httpx.RequestError:
            raise TransportError("Query stream interrupted; the result is incomplete.") from None
        except ValueError:
            raise ResponseError("Invalid query stream frame.") from None
        raise ResponseError("Query stream ended without completion; the result is incomplete.")

    def __iter__(self):
        return self

    def __next__(self) -> list[list[JsonValue]]:
        if self.closed:
            if not self.result.complete:
                raise ResponseError("Query stream closed before completion; the result is incomplete.")
            if self.result.truncated and not self.allow_partial:
                raise ResponseError("Query result exceeded its budget and is incomplete.")
            raise StopIteration
        try:
            frame = self._frame()
            if frame.get("type") == "rows":
                rows = Rows.model_validate(frame).rows
                if any(len(row) != len(self.result.columns) for row in rows):
                    raise ResponseError("Query row has an inconsistent width.")
                self._count += len(rows)
                return rows
            completion = Completion.model_validate(frame)
            if completion.row_count != self._count:
                raise ResponseError("Query completion row count does not match delivered rows.")
            for name, value in completion.model_dump(exclude={"type"}).items():
                setattr(self.result, name, value)
            self.result.complete = True
            self.close()
            if completion.truncated and not self.allow_partial:
                budget = completion.truncation_reason
                limit = self.result.limits.get(budget, "unknown")
                raise ApiError(f"Query result is incomplete: {budget} ({limit}) reached after {self._count} rows. "
                               "Narrow the query or ask the administrator to increase this budget.",
                               status_code=422, code="result_limit")
            raise StopIteration
        except ValidationError:
            self.close()
            raise ResponseError("Invalid query stream frame.") from None
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self.closed:
            self.closed = True
            self.response.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
