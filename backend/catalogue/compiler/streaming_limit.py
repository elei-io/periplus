"""Recognize direct table scans whose work is bounded by a streaming LIMIT."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from sqlglot import exp


@dataclass(frozen=True, slots=True)
class DirectStreamingLimit:
    table: exp.Table
    limit: int
    offset: int

    @property
    def maximum_rows_read(self) -> int:
        return self.limit + self.offset


def direct_streaming_limit(
    query: exp.Expression,
    *,
    bound_parameters: Mapping[str, object] | None = None,
) -> DirectStreamingLimit | None:
    """Prove one direct, unordered, row-preserving table scan is bounded."""

    if not isinstance(query, exp.Select):
        return None
    allowed_select_args = {"expressions", "from_", "limit", "offset"}
    if any(
        value is not None and key not in allowed_select_args
        for key, value in query.args.items()
    ):
        return None
    source_clause = query.args.get("from_")
    if not isinstance(source_clause, exp.From):
        return None
    source = source_clause.this
    if not isinstance(source, exp.Table) or not isinstance(
        source.this,
        exp.Identifier,
    ):
        return None
    if any(
        value is not None and key not in {"alias", "catalog", "db", "this"}
        for key, value in source.args.items()
    ):
        return None
    if any(
        isinstance(node, exp.Query) and node is not query
        for node in query.walk()
    ):
        return None
    if next(query.find_all((exp.AggFunc, exp.Window)), None) is not None:
        return None

    parameters = {
        name.lower(): value
        for name, value in (bound_parameters or {}).items()
    }
    limit_clause = query.args.get("limit")
    limit_expression: exp.Expression | None
    if isinstance(limit_clause, exp.Limit):
        options = limit_clause.args.get("limit_options")
        if _unsafe_limit_options(options):
            return None
        limit_expression = limit_clause.args.get("expression")
    elif isinstance(limit_clause, exp.Fetch):
        options = limit_clause.args.get("limit_options")
        if _unsafe_limit_options(options):
            return None
        limit_expression = limit_clause.args.get("count")
    else:
        return None
    limit = _static_integer(limit_expression, parameters=parameters)
    if limit is None or limit < 0:
        return None

    offset_clause = query.args.get("offset")
    if offset_clause is None:
        offset = 0
    elif isinstance(offset_clause, exp.Offset):
        offset = _static_integer(
            offset_clause.args.get("expression"),
            parameters=parameters,
        )
        if offset is None or offset < 0:
            return None
    else:
        return None
    return DirectStreamingLimit(
        table=source,
        limit=limit,
        offset=offset,
    )


def _unsafe_limit_options(options: object) -> bool:
    return isinstance(options, exp.LimitOptions) and (
        bool(options.args.get("percent"))
        or bool(options.args.get("with_ties"))
    )


def _static_integer(
    expression: exp.Expression | None,
    *,
    parameters: Mapping[str, object],
) -> int | None:
    if isinstance(expression, exp.Paren):
        return _static_integer(expression.this, parameters=parameters)
    if isinstance(expression, exp.Literal) and not expression.is_string:
        try:
            return int(expression.this)
        except ValueError:
            return None
    if isinstance(expression, exp.Placeholder):
        value = parameters.get(expression.name.lower())
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None
    if isinstance(expression, exp.Cast):
        target = expression.args.get("to")
        if not isinstance(target, exp.DataType) or target.sql(
            dialect="duckdb",
        ).upper() not in {
            "BIGINT",
            "HUGEINT",
            "INTEGER",
            "SMALLINT",
            "TINYINT",
            "UBIGINT",
            "UHUGEINT",
            "UINTEGER",
            "USMALLINT",
            "UTINYINT",
        }:
            return None
        return _static_integer(expression.this, parameters=parameters)
    if isinstance(expression, (exp.Add, exp.Mul, exp.Sub)):
        left = _static_integer(expression.this, parameters=parameters)
        right = _static_integer(expression.expression, parameters=parameters)
        if left is None or right is None:
            return None
        if isinstance(expression, exp.Add):
            return left + right
        if isinstance(expression, exp.Sub):
            return left - right
        return left * right
    return None
