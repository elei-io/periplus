"""Compiler-owned parsing for one ordinary read-only DuckDB query."""

from __future__ import annotations

import duckdb
from sqlglot import exp, parse
from sqlglot.errors import ParseError


class CatalogueQueryError(ValueError):
    """Authored SQL is invalid or is not one read query."""


def classify_select(sql: str) -> exp.Query:
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
        raise CatalogueQueryError(f"invalid SQL: {exc}") from exc
    if len(statements) != 1:
        raise CatalogueQueryError("exactly one SQL statement is required")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise CatalogueQueryError("only SELECT queries are allowed")
    return statement
