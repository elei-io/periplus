"""Advisory analysis for user-authored catalogue definitions."""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from .analysis import analyze_catalogue_query
from .errors import (
    OptimizationCode,
    OptimizationDiagnostic,
    QueryOptimizationUnavailable,
)
from .purpose import CatalogueDefinitionPurpose
from .syntax import CatalogueQueryError, classify_select


@dataclass(frozen=True, slots=True, order=True)
class CatalogueDefinitionDependency:
    kind: str
    qualified_name: str
    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogueDefinitionAnalysis:
    normalized_sql: str
    dependencies: tuple[CatalogueDefinitionDependency, ...]


def analyze_catalogue_definition(
    sql: str,
    *,
    purpose: CatalogueDefinitionPurpose,
) -> CatalogueDefinitionAnalysis:
    """Validate a definition body and report its resolved catalogue dependencies."""

    query_sql = _definition_query(sql, purpose=purpose)
    try:
        authored_query = classify_select(query_sql)
    except CatalogueQueryError as exc:
        raise _invalid(str(exc)) from exc
    analysis = analyze_catalogue_query(
        query_sql,
        scalar_macros=purpose.scalar_macros,
        table_macros=purpose.table_macros,
        views=purpose.views,
        scalar_functions=purpose.scalar_functions,
    )
    root = f"{purpose.schema_name}.{purpose.object_name}"
    dependencies = _dependencies(authored_query, purpose=purpose, root=root)
    normalized = (
        analysis.normalized_sql
        if purpose.kind != "scalar_macro"
        else _normalized_scalar(sql)
    )
    return CatalogueDefinitionAnalysis(
        normalized_sql=normalized,
        dependencies=dependencies,
    )


def _definition_query(sql: str, *, purpose: CatalogueDefinitionPurpose) -> str:
    if purpose.kind != "scalar_macro":
        return sql
    if not sql.strip():
        raise _invalid("Scalar macro expressions must not be empty.")
    return f"SELECT {sql}"


def _normalized_scalar(sql: str) -> str:
    try:
        expression = parse_one(sql, dialect="duckdb")
    except ParseError as exc:
        raise _invalid(f"invalid SQL: {exc}") from exc
    return expression.sql(dialect="duckdb", pretty=True)


def _dependencies(
    query: exp.Query,
    *,
    purpose: CatalogueDefinitionPurpose,
    root: str,
) -> tuple[CatalogueDefinitionDependency, ...]:
    known_views = {
        (item.schema_name.lower(), item.view_name.lower()): item
        for item in purpose.views
    }
    known_tables = {
        (item.schema_name.lower(), item.macro_name.lower()): item
        for item in purpose.table_macros
    }
    known_scalars = {
        (item.schema_name.lower(), item.macro_name.lower()): item
        for item in purpose.scalar_macros
    }
    definitions: dict[
        tuple[str, str],
        tuple[str, str, str, bool],
    ] = {}
    for key, item in known_views.items():
        definitions[key] = (
            "view",
            f"{item.schema_name}.{item.view_name}",
            item.sql,
            False,
        )
    for key, item in known_tables.items():
        definitions[key] = (
            "table_macro",
            f"{item.schema_name}.{item.macro_name}",
            item.sql,
            False,
        )
    for key, item in known_scalars.items():
        definitions[key] = (
            "scalar_macro",
            f"{item.schema_name}.{item.macro_name}",
            item.sql,
            True,
        )

    found: set[CatalogueDefinitionDependency] = set()

    def visit(expression: exp.Query, path: tuple[str, ...]) -> None:
        for key in _referenced_definition_keys(expression):
            definition = definitions.get(key)
            if definition is None:
                continue
            kind, qualified, body, scalar = definition
            dependency_path = (*path, qualified)
            found.add(
                CatalogueDefinitionDependency(
                    kind=kind,
                    qualified_name=qualified,
                    path=dependency_path,
                )
            )
            if qualified in path:
                continue
            try:
                child = classify_select(f"SELECT {body}" if scalar else body)
            except CatalogueQueryError:
                continue
            visit(child, dependency_path)

    visit(query, (root,))
    return tuple(sorted(found))


def _referenced_definition_keys(query: exp.Query) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for table in query.find_all(exp.Table):
        table_name = (
            table.this.name
            if isinstance(table.this, exp.Func)
            else table.name
        )
        if table.db and table_name:
            keys.add((table.db.lower(), table_name.lower()))
    for dot in query.find_all(exp.Dot):
        if isinstance(dot.this, exp.Identifier) and isinstance(
            dot.expression, exp.Func
        ):
            keys.add((dot.this.name.lower(), dot.expression.name.lower()))
    return keys


def _invalid(message: str) -> QueryOptimizationUnavailable:
    return QueryOptimizationUnavailable(
        OptimizationDiagnostic(
            code=OptimizationCode.INVALID_QUERY,
            message=message,
            documentation_anchor="catalogue-definitions",
        )
    )
