"""Rewrite ergonomic Atlas DOM helper forms into their storage-level macro signatures."""

from __future__ import annotations

from sqlglot import exp

from repository.catalogue.query import CatalogueQueryError, classify_select


_ATTRIBUTE_HELPERS = frozenset({"get_attribute", "has_attribute"})
_ELEMENT_HELPERS = frozenset({"inner_html", "readable_text", "text_content"})
_SYSTEM_SCALAR_MACROS = frozenset(
    {
        *_ATTRIBUTE_HELPERS,
        *_ELEMENT_HELPERS,
        "has_text",
        "resolve_url",
    }
)


class HelperSqlRewriteError(ValueError):
    """Raised when a shorthand helper has no deterministic element source."""


def rewrite_dom_helpers(sql: str) -> str:
    """Expand shorthand helper calls while leaving existing full signatures untouched.

    ``get_attribute('href')`` and ``readable_text()`` infer the sole visible row source.
    Explicit alias forms such as ``get_attribute(e, 'href')`` and ``readable_text(e)`` are useful
    for joins.  The resulting SQL calls the existing DuckDB macros with their ordinary column
    arguments.
    """

    try:
        statement = classify_select(sql)
    except CatalogueQueryError as exc:
        raise HelperSqlRewriteError(str(exc)) from exc

    functions = [
        function
        for function in statement.find_all(exp.Anonymous)
        if function.name.lower() in _ATTRIBUTE_HELPERS | _ELEMENT_HELPERS
    ]
    tables_by_select: dict[int, dict[str, exp.Table]] = {}
    for function in functions:
        select = function.find_ancestor(exp.Select)
        if select is None:
            continue
        key = id(select)
        tables = tables_by_select.setdefault(key, _select_tables(select))
        name = function.name.lower()
        arguments = function.expressions

        if name in _ATTRIBUTE_HELPERS:
            if len(arguments) == 1:
                alias = _infer_source_alias(tables, name)
                replacement = _function(
                    name,
                    exp.column("attributes", table=alias),
                    arguments[0].copy(),
                )
                function.replace(replacement)
            elif len(arguments) == 2:
                alias = _element_alias(arguments[0], tables)
                if alias is not None:
                    function.replace(
                        _function(
                            name,
                            exp.column("attributes", table=alias),
                            arguments[1].copy(),
                        )
                    )
            continue

        if len(arguments) == 0:
            alias = _infer_source_alias(tables, name)
            function.replace(_element_function(name, alias))
        elif len(arguments) == 1:
            alias = _element_alias(arguments[0], tables)
            if alias is not None:
                function.replace(_element_function(name, alias))

    for function in list(statement.find_all(exp.Anonymous)):
        if (
            function.name.lower() in _SYSTEM_SCALAR_MACROS
            and not isinstance(function.parent, exp.Dot)
        ):
            function.replace(
                exp.Dot(
                    this=exp.to_identifier("macros"),
                    expression=function.copy(),
                )
            )

    return statement.sql(dialect="duckdb")


def _select_tables(select: exp.Select) -> dict[str, exp.Table]:
    tables: dict[str, exp.Table] = {}
    for table in select.find_all(exp.Table):
        if table.find_ancestor(exp.Select) is not select:
            continue
        alias = table.alias_or_name.lower()
        if alias in tables:
            raise HelperSqlRewriteError(f"duplicate table alias {alias!r}")
        tables[alias] = table
    return tables


def _infer_source_alias(tables: dict[str, exp.Table], helper: str) -> str:
    aliases = list(tables)
    if len(aliases) != 1:
        raise HelperSqlRewriteError(
            f"{helper} shorthand requires exactly one visible row source; "
            "pass a source alias explicitly"
        )
    return aliases[0]


def _element_alias(
    expression: exp.Expression,
    tables: dict[str, exp.Table],
) -> str | None:
    if not isinstance(expression, exp.Column) or expression.table:
        return None
    alias = expression.name.lower()
    return alias if alias in tables else None


def _element_function(name: str, alias: str) -> exp.Anonymous:
    return _function(
        name,
        exp.column("document_id", table=alias),
        exp.column("element_index", table=alias),
    )


def _function(name: str, *arguments: exp.Expression) -> exp.Anonymous:
    return exp.Anonymous(this=name, expressions=list(arguments))
