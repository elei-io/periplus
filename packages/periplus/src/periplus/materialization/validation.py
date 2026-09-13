"""Snapshot-pinned validation with bounded scans for content-sorted row checks."""
from contextlib import contextmanager
from sqlglot import exp, parse_one
from pydantic import ByteSize, TypeAdapter
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


def _content_row_checks(sql: str, spec: ProjectionSpec):
    """Partition only a scalar count of independently invalid rows."""
    tree = parse_one(sql, read="duckdb")
    if (spec.ownership_grain != "content" or not spec.sort_order
            or spec.sort_order[0] != "content_sha256 ASC"
            or not isinstance(tree, exp.Select)
            or len(tree.expressions) != 1
            or not isinstance(tree.expressions[0], exp.Count)
            or not isinstance(tree.expressions[0].this, exp.Star)
            or any(tree.args.get(key) for key in ("with_", "joins", "group", "having", "qualify", "limit", "offset"))
            or len(list(tree.find_all(exp.Select))) != 1):
        return None
    source = tree.args.get("from_")
    table = source.this if source is not None else None
    if (not isinstance(table, exp.Table) or table.db != "material"
            or table.name != spec.name or len(list(tree.find_all(exp.Table))) != 1):
        return None
    return tree, table


def _ranges(column: exp.Column, *, content_hash: bool):
    bounds = ([f"{value:02x}" for value in range(4, 256, 4)] if content_hash else
              [chr(value) for value in sorted({32, *range(48, 59), *range(65, 92),
                                             *range(97, 124), 256, 1024, 4096,
                                             8192, 16384, 32768, 65536})])
    if not content_hash:
        # A combined NULL-or-low-term filter defeats native pruning on the
        # production word index. Separate disjoint checks preserve every row.
        yield 0, exp.Is(this=column.copy(), expression=exp.Null())
    for partition in range(len(bounds) + 1):
        if partition == 0 and not content_hash:
            predicate = exp.LT(this=column.copy(), expression=exp.Literal.string(bounds[0]))
        elif partition == 0:
            predicate = exp.or_(exp.Is(this=column.copy(), expression=exp.Null()),
                                exp.LT(this=column.copy(), expression=exp.Literal.string(bounds[0])))
        elif partition == len(bounds):
            predicate = exp.GTE(this=column.copy(), expression=exp.Literal.string(bounds[-1]))
        else:
            predicate = exp.and_(exp.GTE(this=column.copy(), expression=exp.Literal.string(bounds[partition-1])),
                                 exp.LT(this=column.copy(), expression=exp.Literal.string(bounds[partition])))
        yield partition + (not content_hash), predicate


def identity_statements(spec: ProjectionSpec, snapshot: int | None):
    """Equal identities stay in the same range, so duplicates cannot escape."""
    identity = ", ".join(spec.identity_columns)
    sql = f"SELECT count(*) - count(DISTINCT ({identity})) FROM {spec.relation.qualified}"
    leading = spec.sort_order[0].removesuffix(" ASC") if spec.sort_order else ""
    column = next((column for column in spec.columns if column.name == leading), None)
    if leading not in spec.identity_columns or column is None or column.duckdb_type != "VARCHAR":
        yield 0, at_snapshot(sql, snapshot)
        return
    tree = parse_one(sql, read="duckdb")
    for partition, predicate in _ranges(exp.column(leading), content_hash=leading == "content_sha256"):
        yield partition, at_snapshot(tree.copy().where(predicate).sql(dialect="duckdb"), snapshot)


def _checks(spec: ProjectionSpec, snapshot: int | None):
    for index, sql in enumerate(spec.validation_queries):
        parsed = _content_row_checks(sql, spec)
        if parsed is None:
            yield index, 0, at_snapshot(sql, snapshot)
            continue
        tree, table = parsed
        key = exp.column("content_sha256", table=table.alias_or_name)
        # Ordered ranges exploit the existing content sort. Boundary ranges
        # include NULL and noncanonical strings too: validation omits no rows.
        for partition, predicate in _ranges(key, content_hash=True):
            yield index, partition, at_snapshot(tree.copy().where(predicate, append=True).sql(dialect="duckdb"), snapshot)


@contextmanager
def validation_statements(catalogue, spec: ProjectionSpec, *, snapshot: int | None = None):
    """Yield complete checks without materializing every text value in one scan."""
    del catalogue
    yield _checks(spec, snapshot)


def bound_validation_memory(catalogue) -> None:
    """Leave headroom for sibling worker connections and Parquet string buffers."""
    current = catalogue.trusted_remote_rows("SELECT current_setting('memory_limit')")[0][0]
    if TypeAdapter(ByteSize).validate_python(current) > 2_000_000_000:
        catalogue.trusted_remote_execute("SET memory_limit='2GB'")
