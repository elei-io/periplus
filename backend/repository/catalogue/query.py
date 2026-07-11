"""Read-only SQL query execution over the Atlas catalogue."""

from __future__ import annotations

from collections.abc import Iterator
import pyarrow as pa
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from repository.catalogue.client import Catalogue


class CatalogueQueryError(ValueError):
    """Raised when catalogue SQL is invalid or is not a single read query."""


def classify_select(sql: str) -> exp.Query:
    """Parse one DuckDB statement and require a query expression."""

    if not sql.strip():
        raise CatalogueQueryError("SQL must not be empty")
    try:
        statements = [statement for statement in parse(sql, dialect="duckdb") if statement]
    except ParseError as exc:
        raise CatalogueQueryError(f"invalid SQL: {exc}") from exc
    if len(statements) != 1:
        raise CatalogueQueryError("exactly one SQL statement is required")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise CatalogueQueryError("only SELECT queries are allowed")
    return statement


def stream_arrow_query(catalogue: Catalogue, sql: str) -> Iterator[bytes]:
    """Execute validated SQL and yield an Arrow IPC stream in bounded batches."""

    classify_select(sql)
    reader = catalogue.connection.execute(sql).fetch_record_batch(rows_per_batch=65_536)
    sink = _ChunkSink()
    writer = pa.ipc.new_stream(sink, reader.schema)
    try:
        yield from sink.drain()
        for batch in reader:
            writer.write_batch(batch)
            yield from sink.drain()
        writer.close()
        yield from sink.drain()
    finally:
        writer.close()


class _ChunkSink:
    """Minimal file-like Arrow sink that releases bytes after every batch."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.closed = False
        self.position = 0

    def write(self, data: bytes) -> int:
        chunk = bytes(data)
        self.chunks.append(chunk)
        self.position += len(chunk)
        return len(chunk)

    def tell(self) -> int:
        return self.position

    def drain(self) -> Iterator[bytes]:
        chunks, self.chunks = self.chunks, []
        yield from chunks
