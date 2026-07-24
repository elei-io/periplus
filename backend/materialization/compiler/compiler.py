"""Fail-closed compiler for bounded materialization queries."""

from __future__ import annotations

import re

from sqlglot import exp

from repository.catalogue.query import CatalogueQueryError, classify_select

from .errors import (
    Incompatibility,
    IncompatibilityCode,
    IncompatibleQueryError,
)
from .plan import (
    MaterializationPlan,
    MaterializationRefreshStrategy,
    MaterializationScan,
)

_COMPILER_VERSION = 1
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UNSUPPORTED_SELECT_ARGS = (
    "distinct",
    "group",
    "having",
    "joins",
    "laterals",
    "limit",
    "offset",
    "order",
    "qualify",
    "windows",
    "with_",
)
_SELECT_ARG_LABELS = {
    "with_": "common table expressions",
}


def compile_materialization(
    *,
    sql: str,
    source_table: str,
    refresh_strategy: str | MaterializationRefreshStrategy,
    key_columns: tuple[str, ...],
) -> MaterializationPlan:
    """Compile a query only when its bounded-key proof is understood.

    Version one deliberately recognizes one small SQL subset. Adding support
    means turning a structured incompatibility test into a semantic plan test;
    unknown syntax never falls through to execution.
    """

    strategy = _strategy(refresh_strategy)
    if strategy is not MaterializationRefreshStrategy.KEYED:
        _incompatible(
            IncompatibilityCode.UNSUPPORTED_REFRESH_STRATEGY,
            "The compiler currently supports only keyed materializations.",
            sql_fragment=strategy.value,
            documentation_anchor="supported-subset",
        )
    source_table = _identifier(source_table, label="Source table")
    keys = tuple(
        _identifier(column, label="Key column") for column in key_columns
    )
    if not keys:
        _incompatible(
            IncompatibilityCode.KEY_REQUIRED,
            "A keyed materialization requires at least one stable key column.",
            documentation_anchor="stable-output-key",
        )
    if len(set(column.lower() for column in keys)) != len(keys):
        _incompatible(
            IncompatibilityCode.KEY_REQUIRED,
            "Stable key columns must be unique.",
            documentation_anchor="stable-output-key",
        )

    try:
        query = classify_select(sql)
    except CatalogueQueryError as exc:
        _incompatible(
            IncompatibilityCode.INVALID_QUERY,
            str(exc),
            documentation_anchor="query-boundary",
            cause=exc,
        )
    if not isinstance(query, exp.Select):
        _incompatible(
            IncompatibilityCode.UNSUPPORTED_QUERY_SHAPE,
            f"{type(query).__name__} queries are not supported yet.",
            sql_fragment=query.sql(dialect="duckdb"),
            documentation_anchor="supported-subset",
        )

    unsupported = next(
        (
            name
            for name in _UNSUPPORTED_SELECT_ARGS
            if query.args.get(name) is not None
        ),
        None,
    )
    if unsupported is not None:
        node = query.args[unsupported]
        label = _SELECT_ARG_LABELS.get(
            unsupported, unsupported.replace("_", " ")
        )
        _incompatible(
            IncompatibilityCode.UNSUPPORTED_QUERY_SHAPE,
            f"Queries containing {label} are not supported yet.",
            sql_fragment=_sql_fragment(node),
            documentation_anchor="supported-subset",
        )
    nested = next(query.find_all(exp.Subquery), None)
    if nested is not None:
        _incompatible(
            IncompatibilityCode.UNSUPPORTED_QUERY_SHAPE,
            "Subqueries are not supported yet.",
            sql_fragment=nested.sql(dialect="duckdb"),
            documentation_anchor="supported-subset",
        )
    aggregate = next(query.find_all(exp.AggFunc), None)
    if aggregate is not None:
        _incompatible(
            IncompatibilityCode.UNSUPPORTED_QUERY_SHAPE,
            "Aggregate functions are not supported yet.",
            sql_fragment=aggregate.sql(dialect="duckdb"),
            documentation_anchor="supported-subset",
        )
    function = next(query.find_all(exp.Func), None)
    if function is not None:
        _incompatible(
            IncompatibilityCode.UNSUPPORTED_QUERY_SHAPE,
            "Function calls are not supported yet.",
            sql_fragment=function.sql(dialect="duckdb"),
            documentation_anchor="supported-subset",
        )

    tables = tuple(query.find_all(exp.Table))
    matching = tuple(
        table
        for table in tables
        if isinstance(table.this, exp.Identifier)
        and table.name.lower() == source_table.lower()
    )
    if not matching:
        _incompatible(
            IncompatibilityCode.SOURCE_NOT_READ,
            f"The query must directly read its declared driving table "
            f"{source_table!r}.",
            documentation_anchor="driving-table",
        )
    if len(tables) != 1 or len(matching) != 1:
        other = next((table for table in tables if table not in matching), None)
        _incompatible(
            IncompatibilityCode.UNSUPPORTED_RELATION,
            "The compiler currently supports exactly one physical relation scan.",
            sql_fragment=(
                other.sql(dialect="duckdb") if other is not None else None
            ),
            documentation_anchor="supported-subset",
        )

    output_columns: list[str] = []
    preserved: set[str] = set()
    for selection in query.expressions:
        if isinstance(selection, exp.Star) or next(
            selection.find_all(exp.Star), None
        ):
            _incompatible(
                IncompatibilityCode.UNSUPPORTED_PROJECTION,
                "SELECT * is not supported because the compiler must prove "
                "the stable output key explicitly.",
                sql_fragment=selection.sql(dialect="duckdb"),
                documentation_anchor="stable-output-key",
            )
        output_name = selection.alias_or_name
        if not output_name:
            _incompatible(
                IncompatibilityCode.UNSUPPORTED_PROJECTION,
                "Every result expression must have a stable output name.",
                sql_fragment=selection.sql(dialect="duckdb"),
                documentation_anchor="stable-output-key",
            )
        output_columns.append(output_name)
        expression = (
            selection.this if isinstance(selection, exp.Alias) else selection
        )
        if not isinstance(expression, exp.Column):
            _incompatible(
                IncompatibilityCode.UNSUPPORTED_PROJECTION,
                "Compiler version 1 supports only unchanged column projections.",
                sql_fragment=selection.sql(dialect="duckdb"),
                documentation_anchor="supported-subset",
            )
        for key in keys:
            if (
                expression.name.lower() == key.lower()
                and output_name.lower() == key.lower()
            ):
                preserved.add(key.lower())

    if len({column.lower() for column in output_columns}) != len(output_columns):
        _incompatible(
            IncompatibilityCode.UNSUPPORTED_PROJECTION,
            "Result column names must be unique.",
            documentation_anchor="supported-subset",
        )

    missing = tuple(key for key in keys if key.lower() not in preserved)
    if missing:
        _incompatible(
            IncompatibilityCode.KEY_NOT_PRESERVED,
            "The query must project every stable key unchanged; missing "
            + ", ".join(missing)
            + ".",
            sql_fragment=", ".join(missing),
            documentation_anchor="stable-output-key",
        )

    source = matching[0]
    alias = source.alias_or_name or source.name
    return MaterializationPlan(
        compiler_version=_COMPILER_VERSION,
        source_sql=sql.strip(),
        normalized_sql=query.sql(dialect="duckdb", pretty=True),
        source_table=source_table,
        refresh_strategy=strategy,
        key_columns=keys,
        output_columns=tuple(output_columns),
        scans=(
            MaterializationScan(
                relation=source_table,
                alias=alias,
                key_columns=keys,
            ),
        ),
    )


def _strategy(
    value: str | MaterializationRefreshStrategy,
) -> MaterializationRefreshStrategy:
    try:
        return MaterializationRefreshStrategy(value)
    except ValueError as exc:
        _incompatible(
            IncompatibilityCode.UNSUPPORTED_REFRESH_STRATEGY,
            f"Unknown materialization refresh strategy {value!r}.",
            sql_fragment=str(value),
            documentation_anchor="supported-subset",
            cause=exc,
        )


def _identifier(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not _SAFE_IDENTIFIER.fullmatch(normalized):
        _incompatible(
            IncompatibilityCode.UNSUPPORTED_QUERY_SHAPE,
            f"{label} must be an unqualified DuckDB identifier.",
            sql_fragment=value,
            documentation_anchor="query-boundary",
        )
    return normalized


def _sql_fragment(value: object) -> str:
    if isinstance(value, exp.Expression):
        return value.sql(dialect="duckdb")
    if isinstance(value, list):
        return " ".join(
            item.sql(dialect="duckdb")
            if isinstance(item, exp.Expression)
            else str(item)
            for item in value
        )
    return str(value)


def _incompatible(
    code: IncompatibilityCode,
    message: str,
    *,
    sql_fragment: str | None = None,
    documentation_anchor: str | None = None,
    cause: Exception | None = None,
) -> None:
    error = IncompatibleQueryError(
        Incompatibility(
            code=code,
            message=message,
            sql_fragment=sql_fragment,
            documentation_anchor=documentation_anchor,
        )
    )
    if cause is None:
        raise error
    raise error from cause
