"""Recognize aggregates DuckLake can answer without reading table rows."""

from __future__ import annotations

from sqlglot import exp


def metadata_only_row_count_table(query: exp.Expression) -> exp.Table | None:
    """Return the sole table for a direct exact row count, otherwise ``None``."""

    if not isinstance(query, exp.Select):
        return None
    if any(
        query.args.get(key) is not None
        for key in (
            "distinct",
            "group",
            "having",
            "joins",
            "qualify",
            "sample",
            "where",
            "windows",
            "with_",
        )
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
    if len(query.expressions) != 1:
        return None
    projection = query.expressions[0]
    if isinstance(projection, exp.Alias):
        projection = projection.this
    if not isinstance(projection, exp.Count):
        return None
    if projection.this is not None and not isinstance(
        projection.this,
        (exp.Literal, exp.Star),
    ):
        return None
    return source
