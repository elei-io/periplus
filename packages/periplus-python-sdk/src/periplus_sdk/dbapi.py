"""Read-only DB-API 2.0 connection over the public query API.

Each execute is an independent server snapshot. Fetching consumes a bounded stream.
Connections and cursors must not be shared by threads.
"""
from __future__ import annotations

import base64
import builtins
from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from .client import Client
from .errors import ApiError, ConfigurationError, PeriplusError, ResponseError, TransportError
from .stream import StreamResult

apilevel = "2.0"
threadsafety = 1
paramstyle = "qmark"


class Warning(builtins.Warning):
    """DB-API warning."""


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


_INTEGER_TYPES = {f'{prefix}Int{bits}' for prefix in ('', 'U') for bits in (8,16,32,64,128,256)}
_FLOAT_TYPES = {'Float32', 'Float64'}


def _base_type(value: str) -> str:
    while value.startswith(('Nullable(', 'LowCardinality(')):
        value = value[value.index('(')+1:-1]
    return value


class _TypeCategory:
    def __init__(self, names: set[str], prefixes: tuple[str, ...] = ()):
        self.names, self.prefixes = names, prefixes

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, str):return False
        value = _base_type(other)
        return value in self.names or value.startswith(self.prefixes)


STRING = _TypeCategory({'String','UUID','JSON'}, ('FixedString(', 'Enum'))
BINARY = _TypeCategory(set())
NUMBER = _TypeCategory(_INTEGER_TYPES | _FLOAT_TYPES | {'Bool'}, ('Decimal',))
DATETIME = _TypeCategory({'Date','Date32'}, ('DateTime','Time'))
ROWID = _TypeCategory(set())


def _value(value: Any, sql_type: str) -> Any:
    if value is None:return None
    sql_type = _base_type(sql_type)
    if sql_type in _INTEGER_TYPES:return int(value)
    if sql_type in _FLOAT_TYPES:return float(value)
    if sql_type.startswith('Decimal'):return Decimal(str(value))
    if sql_type == 'Bool':return value in (True, 1, '1', 'true')
    if sql_type in ('Date','Date32'):
        try:return date.fromisoformat(value)
        except ValueError:return value
    if sql_type.startswith('DateTime'):
        try:return datetime.fromisoformat(value)
        except ValueError:return value
    if sql_type.startswith('Time'):return time.fromisoformat(value)
    return value


def _parameter(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (Decimal, date, time)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    if isinstance(value, (list, tuple)):
        if any(isinstance(item, (list, tuple, dict)) for item in value):
            raise ProgrammingError("Collection parameters must be one-dimensional lists of scalar values.")
        return [_parameter(item) for item in value]
    raise ProgrammingError("Parameters must be scalar values or scalar lists; use explicit SQL casts for typed values.")


class Connection:
    """Marimo-discoverable, read-only connection; commit is a no-op."""

    dialect = "clickhouse"

    def __init__(self, base_url: str | None = None, *, timeout: float = 620,
                 mode: Literal["stable"] = "stable",
                 schema_version: str | None = None, allow_partial: bool = False):
        try:
            self._client = Client(base_url, timeout=timeout, mode=mode)
        except ConfigurationError as exc:
            raise InterfaceError(str(exc)) from exc
        self.schema_version = schema_version if schema_version is not None else ("public_v1")
        self.allow_partial = allow_partial
        self._cursors = set()
        self.closed = False
        self.last_result: StreamResult | None = None

    def _check(self) -> None:
        if self.closed:
            raise InterfaceError("Connection is closed.")

    def cursor(self) -> Cursor:
        self._check()
        cursor = Cursor(self)
        self._cursors.add(cursor)
        return cursor

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
            for cursor in list(self._cursors):
                cursor.close()
            self._client.close()
            self.closed = True
            self.last_result = None

    def __enter__(self) -> Connection:
        self._check()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def connect(base_url: str | None = None, *, timeout: float = 620,
            mode: Literal["stable"] = "stable",
            schema_version: str | None = None, allow_partial: bool = False) -> Connection:
    return Connection(base_url, timeout=timeout, mode=mode, schema_version=schema_version, allow_partial=allow_partial)


class Cursor:
    """Incremental cursor. Metadata stays available without retaining consumed rows."""

    arraysize = 1

    def __init__(self, connection: Connection):
        self.connection = connection
        self.closed = False
        self.result: StreamResult | None = None
        self.description: list[tuple[Any, ...]] | None = None
        self.rowcount = -1
        self._rows: list[tuple[Any, ...]] = []
        self._position = 0
        self._stream = None

    def _check(self, *, result: bool = False) -> None:
        self.connection._check()
        if self.closed:
            raise InterfaceError("Cursor is closed.")
        if result and self.result is None:
            raise ProgrammingError("Execute a query before fetching rows.")

    def execute(self, operation: str, parameters: Sequence[Any] | None = None) -> Cursor:
        self._check()
        if self._stream is not None:
            self._stream.close()
        self._stream = None
        self.result, self.description, self.rowcount = None, None, -1
        self._rows, self._position = [], 0
        self.connection.last_result = None
        if not isinstance(operation, str):
            raise ProgrammingError("SQL must be a string.")
        if parameters is not None and (not isinstance(parameters, Sequence) or isinstance(parameters, (str, bytes))):
            raise ProgrammingError("Use a positional parameter sequence with ? placeholders.")
        values = [_parameter(v) for v in parameters] if parameters is not None else []
        try:
            self._stream = self.connection._client.stream(operation, values, schema_version=self.connection.schema_version,
                                                          allow_partial=self.connection.allow_partial)
            result = self._stream.result
        except ApiError as exc:
            error = ProgrammingError if exc.code == "sql_invalid" else OperationalError
            raise error(str(exc), status_code=exc.status_code, code=exc.code,
                        retry_after_seconds=exc.retry_after_seconds) from exc
        except TransportError as exc:
            raise OperationalError(str(exc)) from exc
        except ResponseError as exc:
            raise InterfaceError(str(exc)) from exc
        self.result = self.connection.last_result = result
        self.description = [(name, kind, None, None, None, None, None)
                            for name, kind in zip(result.columns, result.types, strict=True)]
        return self

    def _batch(self):
        try:
            batch = next(self._stream)
            self._rows = [tuple(_value(v, t) for v, t in zip(row, self.result.types, strict=True)) for row in batch]
            self._position = 0
            return True
        except StopIteration:
            self.rowcount = -1 if self.result.truncated else self.result.row_count
            self._rows, self._position = [], 0
            return False
        except ApiError as exc:
            raise OperationalError(str(exc), status_code=exc.status_code, code=exc.code,
                                   retry_after_seconds=exc.retry_after_seconds) from exc
        except TransportError as exc:
            raise OperationalError(str(exc)) from exc
        except ResponseError as exc:
            raise InterfaceError(str(exc)) from exc
        except (ValueError, TypeError, ArithmeticError) as exc:
            self._stream.close()
            raise DataError("Query value does not match its SQL type.") from exc

    def fetchone(self) -> tuple[Any, ...] | None:
        self._check(result=True)
        while self._position == len(self._rows):
            if not self._batch():
                return None
        row = self._rows[self._position]
        self._position += 1
        return row

    def fetchmany(self, size: int | None = None) -> list[tuple[Any, ...]]:
        self._check(result=True)
        size = self.arraysize if size is None else size
        if not isinstance(size, int) or size < 0:
            raise ProgrammingError("Fetch size must be a non-negative integer.")
        rows = []
        for _ in range(size):
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)
        return rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        self._check(result=True)
        return list(self)

    def executemany(self, operation: str, seq_of_parameters: Any) -> None:
        self._check()
        raise NotSupportedError("Batch execution is not supported by the read-only query API.")

    def setinputsizes(self, sizes: Any) -> None:
        self._check()

    def setoutputsize(self, size: int, column: int | None = None) -> None:
        self._check()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
        self.connection._cursors.discard(self)
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
