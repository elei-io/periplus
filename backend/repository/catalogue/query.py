"""Read-only SQL query execution over the Atlas catalogue."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

import pyarrow as pa
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from repository.catalogue.client import Catalogue


class CatalogueQueryError(ValueError):
    """Raised when catalogue SQL is invalid or is not a single read query."""


class CatalogueStatementKind(StrEnum):
    QUERY = "query"
    EXPLAIN = "explain"
    EXPLAIN_ANALYZE = "explain_analyze"


@dataclass(frozen=True, slots=True)
class ClassifiedCatalogueStatement:
    kind: CatalogueStatementKind
    sql: str
    query: exp.Query


@dataclass(frozen=True, slots=True)
class CatalogueLintDiagnostic:
    code: str
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class PreparedCatalogueQuery:
    sql: str
    bindings: dict[str, object]
    namespace: str


_DOM_HELPERS = frozenset({"inner_html", "readable_text", "text_content"})
_MANAGED_ROW_TABLES = frozenset({"artifacts", "crawls", "documents", "elements"})
_MAX_DOM_HELPER_INPUT_ROWS = 10_000
_ABSURD_LIMIT = 100_000
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
    "duckdb_",
    "pg_",
    "pragma_",
    "quack_",
    "sqlite_",
)
_EXPLAIN_PREFIX = re.compile(r"(?is)\A(?:\s|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*EXPLAIN\b")


def classify_select(sql: str) -> exp.Query:
    """Parse one DuckDB statement and require a query expression."""

    if not sql.strip():
        raise CatalogueQueryError("SQL must not be empty")
    try:
        statements = [
            statement for statement in parse(sql, dialect="duckdb") if statement
        ]
    except ParseError as exc:
        raise CatalogueQueryError(f"invalid SQL: {exc}") from exc
    if len(statements) != 1:
        raise CatalogueQueryError("exactly one SQL statement is required")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise CatalogueQueryError("only SELECT queries are allowed")
    return statement


def classify_catalogue_statement(sql: str) -> ClassifiedCatalogueStatement:
    """Classify one native query or EXPLAIN statement and validate its inner query."""

    explain = _EXPLAIN_PREFIX.match(sql)
    if not explain:
        query = classify_select(sql)
        return ClassifiedCatalogueStatement(
            kind=CatalogueStatementKind.QUERY,
            sql=sql,
            query=query,
        )

    inner_sql = sql[explain.end() :].strip()
    analyze = re.match(r"(?is)^ANALYZE\b", inner_sql)
    if analyze:
        inner_sql = inner_sql[analyze.end() :].strip()
    query = classify_select(inner_sql)
    return ClassifiedCatalogueStatement(
        kind=(
            CatalogueStatementKind.EXPLAIN_ANALYZE
            if analyze
            else CatalogueStatementKind.EXPLAIN
        ),
        sql=inner_sql,
        query=query,
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


def referenced_catalogue_views(
    statement: ClassifiedCatalogueStatement,
) -> frozenset[str]:
    """Return explicitly referenced public view names."""

    return frozenset(
        table.name
        for table in statement.query.find_all(exp.Table)
        if isinstance(table.this, exp.Identifier)
        and table.db.lower() == "views"
    )


def lint_catalogue_statement(sql: str) -> list[CatalogueLintDiagnostic]:
    """Lint the query contained in a native query or EXPLAIN statement."""

    try:
        classified = classify_catalogue_statement(sql)
    except CatalogueQueryError:
        return []
    return lint_select(classified.sql)


def lint_select(sql: str) -> list[CatalogueLintDiagnostic]:
    """Return advisory performance diagnostics for valid catalogue SQL."""

    try:
        statement = classify_select(sql)
    except CatalogueQueryError:
        return []

    diagnostics: list[CatalogueLintDiagnostic] = []
    bounded_ctes = _bounded_materialized_ctes(statement)
    uses_bounded_source = _uses_outer_cte(statement, bounded_ctes)

    if _uses_outer_dom_helper(statement) and not uses_bounded_source:
        diagnostics.append(
            CatalogueLintDiagnostic(
                code="unbounded_dom_helper",
                severity="warning",
                message=(
                    "DOM helpers may expand across every matching element before an outer "
                    "LIMIT is applied. Select at most 10,000 elements in a MATERIALIZED CTE "
                    "before calling macros.text_content(), macros.readable_text(), or "
                    "macros.inner_html()."
                ),
            )
        )

    large_limits = [
        value
        for query in statement.find_all(exp.Query)
        if (value := _literal_limit(query)) is not None and value > _ABSURD_LIMIT
    ]
    if large_limits:
        diagnostics.append(
            CatalogueLintDiagnostic(
                code="absurd_limit",
                severity="warning",
                message=(
                    f"LIMIT {max(large_limits):,} is unusually large for the interactive SQL "
                    f"workbench. Consider {_ABSURD_LIMIT:,} rows or fewer."
                ),
            )
        )

    if (
        _literal_limit(statement) is None
        and not uses_bounded_source
        and _reads_managed_rows(statement)
        and not _is_single_aggregate(statement)
    ):
        diagnostics.append(
            CatalogueLintDiagnostic(
                code="missing_limit",
                severity="warning",
                message=(
                    "This query may return many rows and has no outer LIMIT. Add a LIMIT for "
                    "interactive exploration."
                ),
            )
        )

    return diagnostics


def _bounded_materialized_ctes(statement: exp.Query) -> set[str]:
    bounded: set[str] = set()
    for cte in statement.find_all(exp.CTE):
        limit = _literal_limit(cte.this) if isinstance(cte.this, exp.Query) else None
        if cte.args.get("materialized") is True and limit is not None:
            if limit <= _MAX_DOM_HELPER_INPUT_ROWS:
                bounded.add(cte.alias_or_name.lower())
    return bounded


def _uses_outer_cte(statement: exp.Query, aliases: set[str]) -> bool:
    if not aliases:
        return False
    return any(
        table.name.lower() in aliases and table.find_ancestor(exp.CTE) is None
        for table in statement.find_all(exp.Table)
    )


def _uses_outer_dom_helper(statement: exp.Query) -> bool:
    for function in statement.find_all(exp.Func):
        if function.find_ancestor(exp.CTE) is not None:
            continue
        name = (
            function.name
            if isinstance(function, exp.Anonymous)
            else function.sql_name()
        )
        if name.lower() in _DOM_HELPERS:
            return True
    return False


def _reads_managed_rows(statement: exp.Query) -> bool:
    return any(
        table.name.lower() in _MANAGED_ROW_TABLES
        for table in statement.find_all(exp.Table)
    )


def _is_single_aggregate(statement: exp.Query) -> bool:
    return (
        isinstance(statement, exp.Select)
        and statement.args.get("group") is None
        and any(statement.find_all(exp.AggFunc))
    )


def _literal_limit(query: exp.Query) -> int | None:
    limit = query.args.get("limit")
    expression = limit.args.get("expression") if isinstance(limit, exp.Limit) else None
    if not isinstance(expression, exp.Literal) or expression.is_string:
        return None
    try:
        return int(expression.this)
    except ValueError:
        return None


def execute_arrow_query(
    catalogue: Catalogue,
    sql: str,
    parameters: dict[str, object] | None = None,
) -> pa.RecordBatchReader:
    """Execute a validated query in the managed catalogue namespace."""

    prepared = prepare_catalogue_query(catalogue, sql, parameters)
    catalogue.connection.execute(f"USE {prepared.namespace}")
    cursor = (
        catalogue.connection.execute(prepared.sql, prepared.bindings)
        if prepared.bindings
        else catalogue.connection.execute(prepared.sql)
    )
    return cursor.to_arrow_reader(batch_size=65_536)


def explain_arrow_query(
    catalogue: Catalogue,
    sql: str,
    parameters: dict[str, object] | None = None,
    *,
    analyze: bool = False,
) -> pa.RecordBatchReader:
    """Return DuckDB's JSON plan, optionally with execution measurements."""

    prepared = prepare_catalogue_query(catalogue, sql, parameters)
    catalogue.connection.execute(f"USE {prepared.namespace}")
    modifier = "ANALYZE, FORMAT JSON" if analyze else "FORMAT JSON"
    explain_sql = f"EXPLAIN ({modifier}) {prepared.sql}"
    cursor = (
        catalogue.connection.execute(explain_sql, prepared.bindings)
        if prepared.bindings
        else catalogue.connection.execute(explain_sql)
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
