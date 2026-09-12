"""Snapshot-pinned scalar validation without occurrence expansion or staging."""
from contextlib import contextmanager
from sqlglot import exp, parse_one
from periplus.materialization.registry import ProjectionSpec

def at_snapshot(sql: str, snapshot: int | None) -> str:
    """Pin physical lake reads without retaining a metadata transaction."""
    if snapshot is None:
        return sql
    if snapshot < 0:
        raise ValueError("snapshot must be nonnegative")
    tree = parse_one(sql, read="duckdb")
    for table in list(tree.find_all(exp.Table)):
        if table.db in {"material", "ingest"}:
            pinned = table.copy()
            pinned.set("alias", None)
            pinned.set("when", exp.HistoricalData(
                this="AT", kind="VERSION", expression=exp.Literal.number(snapshot)
            ))
            # DuckDB does not accept SQLGlot's AT (...) AS alias ordering.
            # Alias the derived relation, keeping the versioned scan inside it.
            replacement = exp.select("*").from_(pinned).subquery(alias=table.alias_or_name)
            if table.args.get("alias"):
                replacement.set("alias", table.args["alias"].copy())
            table.replace(replacement)
    return tree.sql(dialect="duckdb")


@contextmanager
def validation_statements(catalogue, spec: ProjectionSpec, *, snapshot: int | None = None):
    """Yield registry checks against one fixed source snapshot."""
    del catalogue
    yield ((index, 0, at_snapshot(sql, snapshot))
           for index, sql in enumerate(spec.validation_queries))
