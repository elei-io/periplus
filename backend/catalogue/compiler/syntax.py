"""Compiler-owned parsing for one ordinary read-only DuckDB query."""

from __future__ import annotations

import re

import duckdb
from sqlglot import exp, parse
from sqlglot.errors import ParseError

_DUCKDB_READ_EXTENSION = re.compile(
    r"(?is)\A(?:\s|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*(?:PIVOT|UNPIVOT)\b"
)


class CatalogueQueryError(ValueError):
    """Authored SQL is invalid or is not one read query."""


class CatalogueUnsupportedReadError(CatalogueQueryError):
    """DuckDB accepts the read query but the structural parser does not."""


def classify_select(sql: str) -> exp.Expression:
    if not sql.strip():
        raise CatalogueQueryError("SQL must not be empty")
    try:
        duckdb.extract_statements(sql)
    except duckdb.ParserException as exc:
        raise CatalogueQueryError(f"invalid SQL: {exc}") from exc
    try:
        statements = [
            statement for statement in parse(sql, dialect="duckdb") if statement
        ]
    except ParseError as exc:
        if (
            _DUCKDB_READ_EXTENSION.match(sql)
            and ";" not in sql.rstrip().removesuffix(";")
        ):
            raise CatalogueUnsupportedReadError(
                "This valid DuckDB PIVOT/UNPIVOT shape is not structurally "
                "optimized yet."
            ) from exc
        raise CatalogueQueryError(f"invalid SQL: {exc}") from exc
    if len(statements) != 1:
        raise CatalogueQueryError("exactly one SQL statement is required")
    statement = statements[0]
    if not isinstance(statement, (exp.Query, exp.Pivot)):
        raise CatalogueQueryError("only SELECT queries are allowed")
    return statement
