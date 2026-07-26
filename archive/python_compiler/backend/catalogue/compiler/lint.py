"""Non-blocking compiler diagnostics for valid interactive SQL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from sqlglot import exp

from .syntax import CatalogueQueryError, classify_select

_DOM_HELPERS = frozenset({"inner_html", "readable_text", "text_content"})
_MANAGED_ROW_TABLES = frozenset({"artifacts", "crawls", "documents", "elements"})
_MAX_DOM_HELPER_INPUT_ROWS = 10_000
_ABSURD_LIMIT = 100_000
_EXPLAIN_PREFIX = re.compile(
    r"(?is)\A(?:\s|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*EXPLAIN\b"
)


class CatalogueStatementKind(StrEnum):
    QUERY = "query"
    EXPLAIN = "explain"
    EXPLAIN_ANALYZE = "explain_analyze"


@dataclass(frozen=True, slots=True)
class ClassifiedCatalogueStatement:
    kind: CatalogueStatementKind
    sql: str
    query: exp.Expression


@dataclass(frozen=True, slots=True)
class CatalogueLintDiagnostic:
    code: str
    severity: str
    message: str


def classify_catalogue_statement(sql: str) -> ClassifiedCatalogueStatement:
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
    return ClassifiedCatalogueStatement(
        kind=(
            CatalogueStatementKind.EXPLAIN_ANALYZE
            if analyze
            else CatalogueStatementKind.EXPLAIN
        ),
        sql=inner_sql,
        query=classify_select(inner_sql),
    )


def lint_catalogue_statement(sql: str) -> list[CatalogueLintDiagnostic]:
    try:
        classified = classify_catalogue_statement(sql)
    except CatalogueQueryError:
        return []
    return lint_select(classified.sql)


def lint_select(sql: str) -> list[CatalogueLintDiagnostic]:
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
        if (
            cte.args.get("materialized") is True
            and limit is not None
            and limit <= _MAX_DOM_HELPER_INPUT_ROWS
        ):
            bounded.add(cte.alias_or_name.lower())
    return bounded


def _uses_outer_cte(statement: exp.Query, aliases: set[str]) -> bool:
    return bool(aliases) and any(
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
