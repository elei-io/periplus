"""Crawl-graph edge SQL contract and compiler policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Never

from sqlglot import exp

from .analysis import analyze_catalogue_query
from .interactive import (
    InteractiveCompilation,
    compile_interactive_query_with_explanation,
)
from .purpose import GraphEdgePurpose
from .syntax import CatalogueQueryError, classify_select


@dataclass(frozen=True, slots=True)
class GraphEdgeDiagnostic:
    code: str
    message: str
    sql_fragment: str | None = None
    documentation_anchor: str = "graph-edge-query"


class GraphEdgeCompilationError(ValueError):
    """The authored SQL violates the graph navigation contract."""

    def __init__(self, diagnostic: GraphEdgeDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


def compile_graph_edge_query(
    sql: str,
    *,
    purpose: GraphEdgePurpose,
) -> InteractiveCompilation:
    """Validate and optimize an edge query without relaxing edge semantics."""

    statement = _validate_graph_edge_query(sql, purpose=purpose)
    analysis = analyze_catalogue_query(
        statement.sql(dialect="duckdb"),
        scalar_macros=purpose.scalar_macros,
        table_macros=purpose.table_macros,
        views=purpose.views,
        scalar_functions=purpose.scalar_functions,
        bound_scalar_inputs=False,
    )
    return compile_interactive_query_with_explanation(analysis)


def graph_edge_uses_catalogue(sql: str) -> bool:
    """Return whether valid edge SQL reads beyond its current-page package."""

    statement = classify_select(sql)
    cte_names = {
        cte.alias_or_name.lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    for function in statement.find_all(exp.Func):
        if function.name.lower() in {
            "get_attribute",
            "has_attribute",
            "inner_html",
            "readable_text",
            "text_content",
        }:
            return True
    for table in statement.find_all(exp.Table):
        if table.name.lower() in cte_names and not table.db:
            continue
        if _is_page_links(table):
            continue
        return True
    return False


def _validate_graph_edge_query(
    sql: str,
    *,
    purpose: GraphEdgePurpose,
) -> exp.Query:
    try:
        statement = classify_select(sql)
    except CatalogueQueryError as exc:
        _invalid("edge_invalid_query", str(exc))

    placeholders = list(statement.find_all(exp.Placeholder))
    if (
        len(placeholders) != 1
        or placeholders[0].name != "crawl_id"
        or sql.count("$crawl_id") != 1
    ):
        _invalid(
            "edge_crawl_id_required",
            "Edge SQL must bind $crawl_id exactly once.",
            sql_fragment="$crawl_id",
        )

    limit = statement.args.get("limit")
    expression = (
        limit.args.get("expression")
        if isinstance(limit, exp.Limit)
        else None
    )
    if not isinstance(expression, exp.Literal) or expression.is_string:
        _invalid(
            "edge_literal_limit_required",
            "Edge SQL must have an outer literal LIMIT.",
        )
    try:
        limit_value = int(expression.this)
    except ValueError:
        _invalid(
            "edge_invalid_limit",
            "Edge SQL LIMIT must be an integer.",
            sql_fragment=expression.sql(dialect="duckdb"),
        )
    if limit_value < 1 or limit_value > purpose.maximum_limit:
        _invalid(
            "edge_limit_out_of_range",
            f"Edge SQL LIMIT must be between 1 and {purpose.maximum_limit}.",
            sql_fragment=expression.sql(dialect="duckdb"),
        )

    if "url" not in {
        selection.alias_or_name.lower()
        for selection in statement.selects
    }:
        _invalid(
            "edge_url_output_required",
            "Edge SQL must project a column named url.",
        )

    for table in statement.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            _invalid(
                "edge_unmanaged_relation",
                "Edge SQL may only read edge.page_links and catalogue relations.",
                sql_fragment=table.sql(dialect="duckdb"),
            )
    if not any(_is_page_links(table) for table in statement.find_all(exp.Table)):
        _invalid(
            "edge_page_links_required",
            "Edge SQL must read edge.page_links.",
            sql_fragment="edge.page_links",
        )
    return statement


def _is_page_links(table: exp.Table) -> bool:
    return (
        table.db.lower() == "edge"
        and table.name.lower() == "page_links"
    )


def _invalid(
    code: str,
    message: str,
    *,
    sql_fragment: str | None = None,
) -> Never:
    raise GraphEdgeCompilationError(
        GraphEdgeDiagnostic(
            code=code,
            message=message,
            sql_fragment=sql_fragment,
        )
    )
