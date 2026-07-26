"""Read-only SQL query execution over the Atlas catalogue."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import duckdb
import pyarrow as pa
from sqlglot import exp

from atlas_sql import (
    CatalogueLintDiagnostic,
    CatalogueQueryError,
    ClassifiedCatalogueStatement,
    CatalogueStatementKind,
    classify_catalogue_statement,
    classify_select,
    lint_catalogue_statement,
    lint_select,
)
from repository.catalogue.client import Catalogue


@dataclass(frozen=True, slots=True)
class PreparedCatalogueQuery:
    sql: str
    bindings: dict[str, object]
    namespace: str


_INTERACTIVE_SCHEMAS = frozenset({"main", "macros", "views"})
_FORBIDDEN_INTERACTIVE_FUNCTIONS = frozenset(
    {
        "current_setting",
        "duckdb_secrets",
        "duckdb_settings",
        "getenv",
        "glob",
        "getvariable",
        "http_get",
        "http_post",
        "iceberg_scan",
        "mysql_query",
        "mysql_scan",
        "parquet_scan",
        "postgres_query",
        "postgres_scan",
        "query",
        "query_table",
        "quack_query",
        "quack_query_by_name",
        "read_blob",
        "read_csv",
        "read_csv_auto",
        "read_json",
        "read_json_auto",
        "read_ndjson",
        "read_parquet",
        "read_text",
        "sniff_csv",
        "sqlite_scan",
        "which_secret",
        "write_log",
    }
)
_FORBIDDEN_INTERACTIVE_RELATION_PREFIXES = (
    "_atlas_",
    "duckdb_",
    "pg_",
    "pragma_",
    "quack_",
    "sqlite_",
)
def validate_interactive_catalogue_statement(
    sql: str,
    *,
    catalogue_alias: str,
    catalogue_schema: str,
) -> ClassifiedCatalogueStatement:
    """Require a read-only query confined to Atlas-managed catalogue relations."""

    statement = classify_catalogue_statement(sql)
    allowed_schemas = _INTERACTIVE_SCHEMAS | {catalogue_schema.lower()}
    cte_names = {
        cte.alias_or_name.lower()
        for cte in statement.query.find_all(exp.CTE)
        if cte.alias_or_name
    }
    for table in statement.query.find_all(exp.Table):
        catalog = table.catalog.lower()
        schema = table.db.lower()
        if catalog and catalog != catalogue_alias.lower():
            raise CatalogueQueryError(
                "interactive SQL may only read the Atlas catalogue"
            )
        if schema and schema not in allowed_schemas:
            raise CatalogueQueryError(
                "interactive SQL may only read Atlas-managed schemas"
            )
        if isinstance(table.this, exp.Identifier):
            name = table.name.lower()
            if name in cte_names:
                continue
            if name.startswith(_FORBIDDEN_INTERACTIVE_RELATION_PREFIXES):
                raise CatalogueQueryError(
                    "interactive SQL may not read DuckDB system relations"
                )
            continue
        if isinstance(table.this, exp.GenerateSeries):
            continue
        if isinstance(table.this, exp.Anonymous) and schema == "macros":
            continue
        raise CatalogueQueryError(
            "interactive SQL may not read external files or table functions"
        )
    for function in statement.query.find_all(exp.Func):
        if function.name.lower() in _FORBIDDEN_INTERACTIVE_FUNCTIONS:
            raise CatalogueQueryError(f"interactive SQL may not call {function.name}")
    return statement


def trusted_execute_arrow_query(
    catalogue: Catalogue,
    sql: str,
    parameters: dict[str, object] | None = None,
) -> pa.RecordBatchReader:
    """Execute trusted Atlas benchmark SQL in the managed namespace."""

    prepared = prepare_catalogue_query(catalogue, sql, parameters)
    catalogue.trusted_connection.execute(f"USE {prepared.namespace}")
    cursor = (
        catalogue.trusted_connection.execute(prepared.sql, prepared.bindings)
        if prepared.bindings
        else catalogue.trusted_connection.execute(prepared.sql)
    )
    return cursor.to_arrow_reader(batch_size=65_536)


def trusted_explain_arrow_query(
    catalogue: Catalogue,
    sql: str,
    parameters: dict[str, object] | None = None,
    *,
    analyze: bool = False,
) -> pa.RecordBatchReader:
    """Explain trusted Atlas benchmark SQL with optional measurements."""

    prepared = prepare_catalogue_query(catalogue, sql, parameters)
    catalogue.trusted_connection.execute(f"USE {prepared.namespace}")
    modifier = "ANALYZE, FORMAT JSON" if analyze else "FORMAT JSON"
    explain_sql = f"EXPLAIN ({modifier}) {prepared.sql}"
    cursor = (
        catalogue.trusted_connection.execute(explain_sql, prepared.bindings)
        if prepared.bindings
        else catalogue.trusted_connection.execute(explain_sql)
    )
    return cursor.to_arrow_reader(batch_size=65_536)


def prepare_catalogue_query(
    catalogue: Catalogue,
    sql: str,
    parameters: dict[str, object] | None = None,
) -> PreparedCatalogueQuery:
    """Validate public catalogue SQL without executing or rewriting it."""

    statement = classify_select(sql)
    bindings = dict(parameters or {})
    required = {placeholder.name for placeholder in statement.find_all(exp.Placeholder)}
    missing = required - set(bindings)
    if missing:
        names = ", ".join(f"${name}" for name in sorted(missing))
        raise CatalogueQueryError(f"missing required SQL parameter: {names}")
    namespace = ".".join(
        _quote_identifier(part)
        for part in (catalogue.config.alias, catalogue.config.schema)
    )
    return PreparedCatalogueQuery(
        sql=sql,
        bindings=bindings,
        namespace=namespace,
    )


def compile_catalogue_definition(sql: str) -> str:
    """Validate and normalize a self-contained persistent query definition.

    Persistent views and table macros cannot retain connection-local user bindings,
    so placeholders remain forbidden.
    """

    statement = classify_select(sql)
    required = sorted(
        {placeholder.name for placeholder in statement.find_all(exp.Placeholder)}
    )
    if required:
        names = ", ".join(f"${name}" for name in required)
        raise CatalogueQueryError(
            f"persistent SQL definitions cannot contain query parameters: {names}"
        )
    return statement.sql(dialect="duckdb")


def stream_arrow_reader(reader: pa.RecordBatchReader) -> Iterator[bytes]:
    """Yield an executed query's Arrow IPC stream in bounded batches."""

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


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


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
