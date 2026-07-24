"""Purpose-neutral parsing and catalogue-definition resolution."""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from .errors import (
    OptimizationCode,
    OptimizationDiagnostic,
    QueryOptimizationUnavailable,
)
from .macros import (
    bound_scalar_macro_inputs,
    expand_catalogue_macros,
)
from .purpose import (
    ScalarMacroDefinition,
    TableMacroDefinition,
    ViewDefinition,
    ScalarFunctionDefinition,
)
from .relational import (
    FrozenColumnLineage,
    ScopeRelationalFacts,
    analyze_query_lineage,
    analyze_scope_relations,
)
from .syntax import CatalogueQueryError, classify_select


@dataclass(frozen=True, slots=True)
class AnalyzedCatalogueQuery:
    """Resolved ordinary DuckDB query shared by compilation purposes."""

    query: exp.Query
    scopes: tuple[Scope, ...]
    relational_facts: tuple[ScopeRelationalFacts, ...]
    column_lineage: FrozenColumnLineage
    bounded_scalar_input: bool = False

    @property
    def normalized_sql(self) -> str:
        return self.query.sql(dialect="duckdb", pretty=True)

    def facts_for(self, scope: Scope) -> ScopeRelationalFacts:
        for facts in self.relational_facts:
            if facts.scope_id == id(scope):
                return facts
        raise KeyError("Scope does not belong to this analyzed query.")


def analyze_catalogue_query(
    sql: str,
    *,
    scalar_macros: tuple[ScalarMacroDefinition, ...] = (),
    table_macros: tuple[TableMacroDefinition, ...] = (),
    views: tuple[ViewDefinition, ...] = (),
    scalar_functions: tuple[ScalarFunctionDefinition, ...] = (),
    bound_scalar_inputs: bool = False,
) -> AnalyzedCatalogueQuery:
    """Parse authored SQL and resolve authoritative catalogue definitions."""

    try:
        query = classify_select(sql)
    except CatalogueQueryError as exc:
        error = QueryOptimizationUnavailable(
            OptimizationDiagnostic(
                code=OptimizationCode.INVALID_QUERY,
                message=str(exc),
                documentation_anchor="query-boundary",
            )
        )
        raise error from exc
    if not isinstance(query, exp.Query):
        raise QueryOptimizationUnavailable(
            OptimizationDiagnostic(
                code=OptimizationCode.INVALID_QUERY,
                message="Catalogue compilation requires one read-only query.",
                sql_fragment=query.sql(dialect="duckdb"),
                documentation_anchor="query-boundary",
            )
        )
    bounded_query, bounded_scalar_input = (
        bound_scalar_macro_inputs(
            query,
            scalar_definitions=scalar_macros,
            table_definitions=table_macros,
            view_definitions=views,
            scalar_functions=scalar_functions,
        )
        if bound_scalar_inputs
        else (query, False)
    )
    resolved = expand_catalogue_macros(
        bounded_query,
        scalar_definitions=scalar_macros,
        table_definitions=table_macros,
        view_definitions=views,
    )
    scopes = tuple(traverse_scope(resolved))
    relational_facts = tuple(
        analyze_scope_relations(scope) for scope in scopes
    )
    return AnalyzedCatalogueQuery(
        query=resolved,
        scopes=scopes,
        relational_facts=relational_facts,
        column_lineage=analyze_query_lineage(scopes, relational_facts),
        bounded_scalar_input=bounded_scalar_input,
    )


def analyze_resolved_query(query: exp.Query) -> AnalyzedCatalogueQuery:
    """Build shared scope analysis for an already resolved query tree."""

    scopes = tuple(traverse_scope(query))
    relational_facts = tuple(
        analyze_scope_relations(scope) for scope in scopes
    )
    return AnalyzedCatalogueQuery(
        query=query,
        scopes=scopes,
        relational_facts=relational_facts,
        column_lineage=analyze_query_lineage(scopes, relational_facts),
    )
