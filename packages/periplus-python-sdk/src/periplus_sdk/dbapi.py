"""Read-only DB-API 2.0 connection over the public query API.

Each execute is an independent server snapshot. Fetching consumes a bounded local
result, never a remote cursor. Connections and cursors must not be shared by threads.
"""
from __future__ import annotations

import base64
import builtins
from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal
import warnings

from .client import Client
from .errors import ApiError, ConfigurationError, PeriplusError, ResponseError, TransportError
from .types import QueryResult

apilevel = "2.0"
threadsafety = 1
paramstyle = "qmark"


class Warning(builtins.Warning):
    """DB-API warning."""


class TruncationWarning(Warning):
    """The server returned only part of the query result."""


class Error(PeriplusError):
    """Base DB-API error; API failures preserve their safe error attributes."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 code: str | None = None, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retry_after_seconds = retry_after_seconds


class InterfaceError(Error):
    """Invalid connection or wire response."""


class DatabaseError(Error):
    """Query failure."""


class DataError(DatabaseError):
    """A value cannot be represented."""


class OperationalError(DatabaseError):
    """Service, policy or transport failure."""


class IntegrityError(DatabaseError):
    """Integrity constraint failure."""


class InternalError(DatabaseError):
    """Internal query failure."""


class ProgrammingError(DatabaseError):
    """Invalid SQL, parameters or cursor use."""


class NotSupportedError(DatabaseError):
    """Operation is outside the read-only query contract."""


Date = date
Time = time
Timestamp = datetime
Binary = bytes


def DateFromTicks(ticks: float) -> date:
    return datetime.fromtimestamp(ticks).date()


def TimeFromTicks(ticks: float) -> time:
    return datetime.fromtimestamp(ticks).time()


def TimestampFromTicks(ticks: float) -> datetime:
    return datetime.fromtimestamp(ticks)


_INTEGER_TYPES = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT",
                  "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT", "BIGNUM"}
_FLOAT_TYPES = {"FLOAT", "DOUBLE", "REAL"}
_TIME_TYPES = {"TIME", "TIME WITH TIME ZONE", "TIMETZ"}
_TIMESTAMP_TYPES = {"TIMESTAMP", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS",
                    "TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ"}


class _TypeCategory:
    def __init__(self, names: set[str], prefix: str = ""):
        self.names, self.prefix = names, prefix

    def __eq__(self, other: object) -> bool:
        return isinstance(other, str) and (other in self.names or bool(self.prefix and other.startswith(self.prefix)))


STRING = _TypeCategory({"VARCHAR", "UUID", "JSON", "ENUM"})
BINARY = _TypeCategory({"BLOB"})
NUMBER = _TypeCategory(_INTEGER_TYPES | _FLOAT_TYPES | {"BOOLEAN"}, "DECIMAL(")
DATETIME = _TypeCategory({"DATE"} | _TIME_TYPES | _TIMESTAMP_TYPES)
ROWID = _TypeCategory(set())


def _value(value: Any, sql_type: str) -> Any:
    if value is None:
        return None
    if sql_type in _INTEGER_TYPES:
        return int(value)
    if sql_type in _FLOAT_TYPES:
        return float(value)
    if sql_type.startswith("DECIMAL("):
        return Decimal(str(value))
    # Preserve infinities and out-of-range dates rather than clipping them.
    if sql_type == "DATE":
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    if sql_type in _TIME_TYPES:
        return time.fromisoformat(value)
    if sql_type in _TIMESTAMP_TYPES:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    if sql_type == "BLOB":
        return base64.b64decode(value, validate=True)
    # UUIDs remain strings, and nested/other types retain their JSON wire values.
    return value


def _parameter(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (Decimal, date, time)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    raise ProgrammingError("Parameters must be scalar JSON values, Decimal, date, time or datetime; use explicit SQL casts for typed strings.")


class Connection:
    """Marimo-discoverable, read-only connection; commit is a no-op."""

    dialect = "duckdb"

    def __init__(self, base_url: str | None = None, *, timeout: float = 140,
                 mode: Literal["stable", "experimental"] = "stable",
                 schema_version: str = "public_v1"):
        try:
            self._client = Client(base_url, timeout=timeout, mode=mode)
        except ConfigurationError as exc:
            raise InterfaceError(str(exc)) from exc
        self.schema_version = schema_version
        self.closed = False
        self.last_result: QueryResult | None = None

    def _check(self) -> None:
        if self.closed:
            raise InterfaceError("Connection is closed.")

    def cursor(self) -> Cursor:
        self._check()
        return Cursor(self)

    def execute(self, operation: str, parameters: Sequence[Any] | None = None) -> Cursor:
        cursor = self.cursor()
        try:
            return cursor.execute(operation, parameters)
        except BaseException:
            cursor.close()
            raise

    def commit(self) -> None:
        """No-op: each read executes in its own server transaction."""
        self._check()

    def rollback(self) -> None:
        self._check()
        raise NotSupportedError("Periplus has no client transactions to roll back.")

    def close(self) -> None:
        if not self.closed:
            self._client.close()
            self.closed = True
            self.last_result = None

    def __enter__(self) -> Connection:
        self._check()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def connect(base_url: str | None = None, *, timeout: float = 140,
            mode: Literal["stable", "experimental"] = "stable",
            schema_version: str = "public_v1") -> Connection:
    return Connection(base_url, timeout=timeout, mode=mode, schema_version=schema_version)


class Cursor:
    """A buffered result. Metadata stays on result after rows are consumed."""

    arraysize = 1

    def __init__(self, connection: Connection):
        self.connection = connection
        self.closed = False
        self.result: QueryResult | None = None
        self.description: list[tuple[Any, ...]] | None = None
        self.rowcount = -1
        self._rows: list[tuple[Any, ...]] = []
        self._position = 0

    def _check(self, *, result: bool = False) -> None:
        self.connection._check()
        if self.closed:
            raise InterfaceError("Cursor is closed.")
        if result and self.result is None:
            raise ProgrammingError("Execute a query before fetching rows.")

    def execute(self, operation: str, parameters: Sequence[Any] | None = None) -> Cursor:
        self._check()
        self.result, self.description, self.rowcount = None, None, -1
        self._rows, self._position = [], 0
        self.connection.last_result = None
        if not isinstance(operation, str):
            raise ProgrammingError("SQL must be a string.")
        if parameters is not None and (not isinstance(parameters, Sequence) or isinstance(parameters, (str, bytes))):
            raise ProgrammingError("Use a positional parameter sequence with ? placeholders.")
        values = [_parameter(v) for v in parameters] if parameters is not None else []
        try:
            result = self.connection._client.execute(operation, values, schema_version=self.connection.schema_version)
        except ApiError as exc:
            error = ProgrammingError if exc.code == "sql_invalid" else OperationalError
            raise error(str(exc), status_code=exc.status_code, code=exc.code,
                        retry_after_seconds=exc.retry_after_seconds) from exc
        except TransportError as exc:
            raise OperationalError(str(exc)) from exc
        except ResponseError as exc:
            raise InterfaceError(str(exc)) from exc
        if len(result.columns) != len(result.types) or any(len(r) != len(result.columns) for r in result.rows):
            raise InterfaceError("Query columns, types and rows have inconsistent widths.")
        try:
            rows = [tuple(_value(v, t) for v, t in zip(row, result.types, strict=True)) for row in result.rows]
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise DataError("Query value does not match its SQL type.") from exc
        self.result = self.connection.last_result = result
        self.description = [(name, kind, None, None, None, None, None)
                            for name, kind in zip(result.columns, result.types, strict=True)]
        self._rows = rows
        self.rowcount = -1 if result.truncated else len(rows)
        if result.truncated:
            warnings.warn(f"Periplus returned a truncated result ({len(rows)} rows); inspect connection.last_result or cursor.result. Fetching does not retrieve additional rows.",
                          TruncationWarning, stacklevel=2)
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        self._check(result=True)
        if self._position == len(self._rows):
            return None
        row = self._rows[self._position]
        self._position += 1
        return row

    def fetchmany(self, size: int | None = None) -> list[tuple[Any, ...]]:
        self._check(result=True)
        size = self.arraysize if size is None else size
        if not isinstance(size, int) or size < 0:
            raise ProgrammingError("Fetch size must be a non-negative integer.")
        end = min(self._position + size, len(self._rows))
        rows = self._rows[self._position:end]
        self._position = end
        return rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        self._check(result=True)
        return self.fetchmany(len(self._rows) - self._position)

    def executemany(self, operation: str, seq_of_parameters: Any) -> None:
        self._check()
        raise NotSupportedError("Batch execution is not supported by the read-only query API.")

    def setinputsizes(self, sizes: Any) -> None:
        self._check()

    def setoutputsize(self, size: int, column: int | None = None) -> None:
        self._check()

    def close(self) -> None:
        self.closed = True
        self._rows = []
        self.result = None
        self.description = None
        self.rowcount = -1

    def __iter__(self) -> Cursor:
        self._check(result=True)
        return self

    def __next__(self) -> tuple[Any, ...]:
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row

    def __enter__(self) -> Cursor:
        self._check()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
