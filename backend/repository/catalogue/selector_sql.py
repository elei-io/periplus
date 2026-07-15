"""Rewrite Atlas ``css_select(alias, 'selector')`` SQL into relational SQL.

The function-shaped syntax is an Atlas SQL special form, not a DuckDB scalar UDF.  This module is
intentionally disconnected from query execution while its contract is exercised and measured.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from sqlglot import exp, parse_one

from repository.catalogue.query import CatalogueQueryError, classify_select
from repository.catalogue.selectors import (
    SelectorCompileError,
    SelectorScopeRequired,
    compile_selector,
    compile_selector_predicate,
)


_PARAMETER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class SelectorSqlRewriteError(ValueError):
    """Raised when ``css_select`` cannot be rewritten safely."""


@dataclass(frozen=True, slots=True)
class RewrittenSelectorSql:
    """Ordinary DuckDB SQL plus bindings introduced by selector compilation."""

    sql: str
    parameters: dict[str, object]
    required_parameters: tuple[str, ...]
    unbounded_structural_selectors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Scope:
    kind: Literal["document", "crawl"]
    parameter: str | None
    literal: object | None
    literal_node: exp.Literal | None

    @property
    def key(self) -> tuple[object, ...]:
        value = self.parameter if self.parameter is not None else self.literal
        return (self.kind, value)


@dataclass(frozen=True, slots=True)
class _Call:
    node: exp.Anonymous
    alias: str
    selector: str
    scope: _Scope | None


def rewrite_css_select(
    sql: str,
    *,
    namespaces: dict[str | None, str] | None = None,
) -> RewrittenSelectorSql:
    """Compile boolean ``css_select`` calls without executing the resulting query.

    A one-argument call infers its target when exactly one outer ``elements`` source exists.  A
    two-argument call names an explicit alias, which is required when multiple element sources are
    present.  The selector must be a string literal.  Every target must be constrained by a
    top-level document predicate, either directly or through a crawl-to-document join.  Proven
    bounds are pushed inside structural selector CTEs.  Without one, structural selectors still
    compile globally and are reported to callers as a performance-lint condition.
    """

    try:
        statement = classify_select(sql)
    except CatalogueQueryError as exc:
        raise SelectorSqlRewriteError(str(exc)) from exc

    functions = [
        function
        for function in statement.find_all(exp.Anonymous)
        if function.name.lower() == "css_select"
    ]
    if not functions:
        return RewrittenSelectorSql(
            sql=statement.sql(dialect="duckdb"),
            parameters={},
            required_parameters=tuple(sorted(_placeholder_names(statement))),
        )
    if not isinstance(statement, exp.Select):
        raise SelectorSqlRewriteError("css_select is only supported in an outer SELECT")

    tables = _outer_tables(statement)
    calls = [_validate_call(function, statement, tables) for function in functions]
    existing_parameters = _placeholder_names(statement)
    existing_names = set(tables) | {
        cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)
    }
    existing_names.update(
        subquery.alias.lower()
        for subquery in statement.find_all(exp.Subquery)
        if subquery.alias and subquery.find_ancestor(exp.Select) is statement
    )
    generated: dict[str, object] = {}
    compiled_locals: dict[tuple[str, str], exp.Expression] = {}
    compiled_relations: dict[tuple[object, ...], tuple[str, str]] = {}
    materialized: list[tuple[str, exp.Query]] = []
    unbounded_structural: set[str] = set()

    for call in calls:
        local_key = (call.alias, call.selector)
        local_predicate = compiled_locals.get(local_key)
        if local_predicate is None:
            prefix_index = len(compiled_locals) + len(compiled_relations)
            while True:
                prefix = f"atlas_css_{prefix_index}"
                try:
                    compiled_local = compile_selector_predicate(
                        call.selector,
                        subject=call.alias,
                        namespaces=namespaces,
                        parameter_prefix=prefix,
                    )
                except SelectorScopeRequired:
                    break
                except SelectorCompileError as exc:
                    raise SelectorSqlRewriteError(str(exc)) from exc
                if not (
                    set(compiled_local.parameters)
                    & (existing_parameters | set(generated))
                ):
                    local_predicate = parse_one(
                        compiled_local.sql,
                        dialect="duckdb",
                        into=exp.Condition,
                    )
                    generated.update(compiled_local.parameters)
                    compiled_locals[local_key] = local_predicate
                    break
                prefix_index += 1
        if local_predicate is not None:
            call.node.replace(local_predicate.copy())
            continue

        scope_key = call.scope.key if call.scope is not None else ("global",)
        relation_key = (call.selector, *scope_key)
        relation = compiled_relations.get(relation_key)
        if relation is None:
            relation_index = len(compiled_relations)
            match_name = _unique_name(
                f"atlas_css_match_{relation_index}", existing_names
            )
            existing_names.add(match_name)

            scope_parameter = call.scope.parameter if call.scope is not None else None
            if call.scope is not None and scope_parameter is None:
                scope_parameter = _unique_parameter(
                    f"atlas_css_scope_{relation_index}",
                    existing_parameters | set(generated),
                )
                generated[scope_parameter] = call.scope.literal
                if call.scope.literal_node is not None:
                    call.scope.literal_node.replace(exp.Placeholder(this=scope_parameter))

            temporary_document_parameter = scope_parameter
            if call.scope is not None and call.scope.kind == "crawl":
                temporary_document_parameter = _unique_parameter(
                    f"atlas_css_document_{relation_index}",
                    existing_parameters | set(generated),
                )

            prefix_index = relation_index
            while True:
                prefix = f"atlas_css_{prefix_index}"
                try:
                    compiled = compile_selector(
                        call.selector,
                        namespaces=namespaces,
                        parameter_prefix=prefix,
                        document_parameter=temporary_document_parameter,
                    )
                except SelectorCompileError as exc:
                    raise SelectorSqlRewriteError(str(exc)) from exc
                if not (set(compiled.parameters) & (existing_parameters | set(generated))):
                    break
                prefix_index += 1

            generated.update(compiled.parameters)
            compiled_query = classify_select(compiled.sql)
            if call.scope is not None and call.scope.kind == "crawl":
                document_lookup = _crawl_document_lookup(scope_parameter)
                compiled_query = compiled_query.transform(
                    lambda node: document_lookup.copy()
                    if isinstance(node, exp.Placeholder)
                    and node.this == temporary_document_parameter
                    else node,
                    copy=False,
                )
            if call.scope is None:
                unbounded_structural.add(call.selector)
            match_alias = _unique_name(
                f"atlas_css_rows_{relation_index}", existing_names
            )
            existing_names.add(match_alias)
            relation = (match_name, match_alias)
            compiled_relations[relation_key] = relation
            materialized.append((match_name, compiled_query))

        match_name, match_alias = relation
        call.node.replace(_membership_predicate(match_name, match_alias, call.alias))

    rewritten: exp.Query = statement
    for match_name, compiled_query in materialized:
        rewritten = rewritten.with_(
            match_name,
            as_=compiled_query,
            materialized=True,
            append=True,
            copy=False,
        )

    required = _placeholder_names(rewritten) - set(generated)
    return RewrittenSelectorSql(
        sql=rewritten.sql(dialect="duckdb"),
        parameters=generated,
        required_parameters=tuple(sorted(required)),
        unbounded_structural_selectors=tuple(sorted(unbounded_structural)),
    )


def _validate_call(
    function: exp.Anonymous,
    statement: exp.Select,
    tables: dict[str, exp.Table],
) -> _Call:
    if function.find_ancestor(exp.Select) is not statement:
        raise SelectorSqlRewriteError("css_select is only supported in the outer SELECT")
    arguments = function.expressions
    inferred = len(arguments) == 1
    if inferred:
        selector = arguments[0]
        element_sources = [
            (alias, table)
            for alias, table in tables.items()
            if table.name.lower() == "elements"
        ]
        if len(element_sources) != 1:
            raise SelectorSqlRewriteError(
                "one-argument css_select requires exactly one outer elements source"
            )
        alias, table = element_sources[0]
    elif len(arguments) == 2:
        target, selector = arguments
        if not isinstance(target, exp.Column) or target.table:
            raise SelectorSqlRewriteError(
                "css_select first argument must be a bare elements alias"
            )
        alias = target.name.lower()
        table = tables.get(alias)
        if table is None:
            raise SelectorSqlRewriteError(f"unknown css_select table alias {target.name!r}")
        if table.name.lower() != "elements":
            raise SelectorSqlRewriteError("css_select can only target the elements table")
        if table.args.get("alias") is None:
            raise SelectorSqlRewriteError("two-argument css_select requires an explicit alias")
    else:
        raise SelectorSqlRewriteError(
            "css_select requires a selector literal and optional elements alias"
        )
    if not isinstance(selector, exp.Literal) or not selector.is_string:
        raise SelectorSqlRewriteError("CSS selector must be a string literal")
    scope = _find_scope(
        statement,
        alias,
        tables,
        allow_unqualified=inferred and len(tables) == 1,
    )
    return _Call(function, alias, selector.this, scope)


def _outer_tables(statement: exp.Select) -> dict[str, exp.Table]:
    tables: dict[str, exp.Table] = {}
    for table in statement.find_all(exp.Table):
        if table.find_ancestor(exp.Select) is not statement:
            continue
        alias = table.alias_or_name.lower()
        if alias in tables:
            raise SelectorSqlRewriteError(f"duplicate table alias {alias!r}")
        tables[alias] = table
    return tables


def _find_scope(
    statement: exp.Select,
    element_alias: str,
    tables: dict[str, exp.Table],
    *,
    allow_unqualified: bool,
) -> _Scope | None:
    where = statement.args.get("where")
    conjuncts = _conjuncts(where.this) if isinstance(where, exp.Where) else []
    direct = [
        bound
        for predicate in conjuncts
        if (
            bound := _bound_value(
                predicate,
                element_alias,
                "document_id",
                allow_unqualified=allow_unqualified,
            )
        )
        is not None
    ]
    if len(direct) == 1:
        scope = _scope_from_bound("document", direct[0])
        if scope is not None:
            return scope

    crawl_aliases = [
        alias for alias, table in tables.items() if table.name.lower() == "crawls"
    ]
    joined = [
        alias
        for alias in crawl_aliases
        if _aliases_share_document(statement, alias, element_alias)
    ]
    crawl_bounds: list[tuple[exp.Expression, exp.Expression]] = []
    for alias in joined:
        for predicate in conjuncts:
            bound = _bound_value(predicate, alias, "crawl_id")
            if bound is not None:
                crawl_bounds.append(bound)
    if len(crawl_bounds) == 1:
        return _scope_from_bound("crawl", crawl_bounds[0])
    return None


def _scope_from_bound(
    kind: Literal["document", "crawl"],
    bound: tuple[exp.Expression, exp.Expression],
) -> _Scope | None:
    _column, value = bound
    if isinstance(value, exp.Placeholder):
        name = value.this
        if not isinstance(name, str) or not _PARAMETER_NAME.fullmatch(name):
            return None
        return _Scope(kind, name, None, None)
    if isinstance(value, exp.Literal) and value.is_string:
        return _Scope(kind, None, value.this, value)
    return None


def _bound_value(
    predicate: exp.Expression,
    table: str,
    column: str,
    *,
    allow_unqualified: bool = False,
) -> tuple[exp.Expression, exp.Expression] | None:
    if not isinstance(predicate, exp.EQ):
        return None
    left, right = predicate.this, predicate.expression
    if _is_column(left, table, column, allow_unqualified=allow_unqualified):
        return left, right
    if _is_column(right, table, column, allow_unqualified=allow_unqualified):
        return right, left
    return None


def _aliases_share_document(
    statement: exp.Select,
    left_alias: str,
    right_alias: str,
) -> bool:
    predicates: list[exp.Expression] = []
    where = statement.args.get("where")
    if isinstance(where, exp.Where):
        predicates.extend(_conjuncts(where.this))
    for join in statement.args.get("joins") or []:
        on = join.args.get("on")
        if on is not None:
            predicates.extend(_conjuncts(on))
    for predicate in predicates:
        if not isinstance(predicate, exp.EQ):
            continue
        if (
            _is_column(predicate.this, left_alias, "document_id")
            and _is_column(predicate.expression, right_alias, "document_id")
        ) or (
            _is_column(predicate.this, right_alias, "document_id")
            and _is_column(predicate.expression, left_alias, "document_id")
        ):
            return True

    from_clause = statement.args.get("from_")
    source = from_clause.this if isinstance(from_clause, exp.From) else None
    prior_aliases = {source.alias_or_name.lower()} if isinstance(source, exp.Table) else set()
    for join in statement.args.get("joins") or []:
        joined = join.this
        joined_alias = joined.alias_or_name.lower() if isinstance(joined, exp.Table) else None
        using = join.args.get("using") or []
        uses_document_id = any(item.name.lower() == "document_id" for item in using)
        if uses_document_id and joined_alias is not None:
            if (
                joined_alias == left_alias
                and right_alias in prior_aliases
                or joined_alias == right_alias
                and left_alias in prior_aliases
            ):
                return True
        if joined_alias is not None:
            prior_aliases.add(joined_alias)
    return False


def _conjuncts(expression: exp.Expression) -> list[exp.Expression]:
    if isinstance(expression, exp.And):
        return _conjuncts(expression.this) + _conjuncts(expression.expression)
    return [expression]


def _is_column(
    expression: exp.Expression,
    table: str,
    name: str,
    *,
    allow_unqualified: bool = False,
) -> bool:
    return (
        isinstance(expression, exp.Column)
        and expression.name.lower() == name.lower()
        and (
            expression.table.lower() == table.lower()
            or allow_unqualified
            and not expression.table
        )
    )


def _crawl_document_lookup(crawl_parameter: str) -> exp.Subquery:
    lookup = (
        exp.select(exp.column("document_id", table="atlas_css_crawl"))
        .from_(exp.Table(this="crawls", alias="atlas_css_crawl"))
        .where(
            exp.EQ(
                this=exp.column("crawl_id", table="atlas_css_crawl"),
                expression=exp.Placeholder(this=crawl_parameter),
            )
        )
    )
    return exp.Subquery(this=lookup)


def _membership_predicate(
    match_name: str,
    match_alias: str,
    element_alias: str,
) -> exp.Exists:
    predicate = exp.and_(
        exp.EQ(
            this=exp.column("document_id", table=match_alias),
            expression=exp.column("document_id", table=element_alias),
        ),
        exp.EQ(
            this=exp.column("element_index", table=match_alias),
            expression=exp.column("element_index", table=element_alias),
        ),
    )
    query = (
        exp.select(exp.Literal.number(1))
        .from_(exp.Table(this=match_name, alias=match_alias))
        .where(predicate)
    )
    return exp.Exists(this=query)


def _placeholder_names(expression: exp.Expression) -> set[str]:
    return {
        placeholder.this
        for placeholder in expression.find_all(exp.Placeholder)
        if isinstance(placeholder.this, str)
    }


def _unique_name(candidate: str, occupied: set[str]) -> str:
    name = candidate
    suffix = 1
    while name.lower() in occupied:
        name = f"{candidate}_{suffix}"
        suffix += 1
    return name


def _unique_parameter(candidate: str, occupied: set[str]) -> str:
    name = candidate
    suffix = 1
    while name in occupied:
        name = f"{candidate}_{suffix}"
        suffix += 1
    return name
