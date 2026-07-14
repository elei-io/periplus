"""Read-only SQL query execution over the Atlas catalogue."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING
import pyarrow as pa
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from repository.catalogue.client import Catalogue

if TYPE_CHECKING:
    from repository.catalogue.selector_sql import RewrittenSelectorSql


class CatalogueQueryError(ValueError):
    """Raised when catalogue SQL is invalid or is not a single read query."""


@dataclass(frozen=True, slots=True)
class CatalogueLintDiagnostic:
    code: str
    severity: str
    message: str


_DOM_HELPERS = frozenset({"inner_html", "readable_text", "text_content"})
_DOM_SQL_SPECIAL_FORMS = frozenset(
    {"css_select", "get_attribute", "has_attribute", *_DOM_HELPERS}
)
_MANAGED_ROW_TABLES = frozenset({"artifacts", "crawls", "documents", "elements"})
_MAX_DOM_HELPER_INPUT_ROWS = 10_000
_ABSURD_LIMIT = 100_000


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


def lint_select(sql: str) -> list[CatalogueLintDiagnostic]:
    """Return advisory performance diagnostics for valid catalogue SQL."""

    try:
        statement = classify_select(sql)
    except CatalogueQueryError:
        return []

    rewritten = None
    anonymous_names = {
        function.name.lower() for function in statement.find_all(exp.Anonymous)
    }
    if anonymous_names & _DOM_SQL_SPECIAL_FORMS:
        try:
            rewritten = _rewrite_select(sql)
        except CatalogueQueryError as exc:
            return [
                CatalogueLintDiagnostic(
                    code=(
                        "invalid_css_select"
                        if "css_select" in anonymous_names
                        else "invalid_dom_helper"
                    ),
                    severity="error",
                    message=str(exc),
                )
            ]

    diagnostics: list[CatalogueLintDiagnostic] = []
    if rewritten is not None and rewritten.unbounded_structural_selectors:
        selectors = ", ".join(
            repr(selector) for selector in rewritten.unbounded_structural_selectors
        )
        diagnostics.append(
            CatalogueLintDiagnostic(
                code="unbounded_structural_css",
                severity="warning",
                message=(
                    f"Structural CSS selector {selectors} has no document or crawl bound. "
                    "Atlas will run it globally, which may scan and partition billions of "
                    "element rows. Add a document_id or crawl_id predicate when practical."
                ),
            )
        )
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
                    "before calling text_content(), readable_text(), or inner_html()."
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
                    f"workbench. Consider { _ABSURD_LIMIT:,} rows or fewer."
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
        name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
        if name.lower() in _DOM_HELPERS:
            return True
    return False


def _reads_managed_rows(statement: exp.Query) -> bool:
    return any(table.name.lower() in _MANAGED_ROW_TABLES for table in statement.find_all(exp.Table))


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

    rewritten = _rewrite_select(sql)
    bindings = dict(parameters or {})
    bindings.update(rewritten.parameters)
    missing = set(rewritten.required_parameters) - set(bindings)
    if missing:
        names = ", ".join(f"${name}" for name in sorted(missing))
        raise CatalogueQueryError(f"missing required SQL parameter: {names}")
    namespace = ".".join(
        _quote_identifier(part)
        for part in (catalogue.config.alias, catalogue.config.schema)
    )
    catalogue.connection.execute(f"USE {namespace}")
    cursor = (
        catalogue.connection.execute(rewritten.sql, bindings)
        if bindings
        else catalogue.connection.execute(rewritten.sql)
    )
    return cursor.to_arrow_reader(batch_size=65_536)


def _rewrite_select(sql: str) -> RewrittenSelectorSql:
    # Local import keeps the standalone AST layer dependent on query classification without
    # introducing a module-import cycle when execution opts into the rewrite.
    from repository.catalogue.selector_sql import (
        SelectorSqlRewriteError,
        rewrite_css_select,
    )
    from repository.catalogue.helper_sql import (
        HelperSqlRewriteError,
        rewrite_dom_helpers,
    )

    try:
        return rewrite_css_select(rewrite_dom_helpers(sql))
    except (HelperSqlRewriteError, SelectorSqlRewriteError) as exc:
        raise CatalogueQueryError(str(exc)) from exc


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
