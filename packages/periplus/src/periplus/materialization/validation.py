"""Partition expanding validation inputs once, outside persistent lake storage."""
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlglot import exp, parse_one

from periplus.materialization.registry import PROJECTIONS, ProjectionSpec
from periplus.materialization.sql import sql_string
from periplus.platform.telemetry import event


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


def _expanding(sql: str) -> bool:
    return any(isinstance(node, (exp.Unnest, exp.Explode))
               for node in parse_one(sql, read="duckdb").walk())


def _sources(sql: str, spec: ProjectionSpec) -> tuple[exp.Expression, list[exp.Table]]:
    tree = parse_one(sql, read="duckdb")
    tables = list(tree.find_all(exp.Table))
    # The supported expanding contracts count source-row defects and equijoin
    # references on content identity. Fail closed for a different contract.
    if (len(tree.expressions) != 1 or not isinstance(tree.expressions[0], exp.Count)
            or not isinstance(tree.expressions[0].this, exp.Star)
            or tree.args.get('limit') or tree.args.get('group') or tree.args.get('having')):
        raise ValueError("expanding validation requires a row-defect count")
    if any(isinstance(node, (exp.Group, exp.Having, exp.Limit, exp.Offset,
                             exp.Distinct, exp.Window, exp.SetOperation))
           or (isinstance(node, exp.AggFunc) and node is not tree.expressions[0])
           for node in tree.walk()):
        raise ValueError("expanding validation must preserve source-row defects")
    if sum(t.db == 'material' and t.name == spec.name for t in tables) != 1:
        raise ValueError("expanding validation requires one validated source relation")
    registered = {p.name: p for p in PROJECTIONS}
    for table in tables:
        relation = registered.get(table.name)
        if (table.db != 'material' or relation is None
                or 'content_sha256' not in relation.identity_columns):
            raise ValueError("expanding validation sources must have content identities")
    for join in tree.find_all(exp.Join):
        if 'content_sha256' not in [key.name for key in join.args.get('using') or []]:
            raise ValueError("partitioned validation joins must equate content identities")
    return tree, tables


@contextmanager
def validation_statements(catalogue, spec: ProjectionSpec, *, partitions: int = 64, snapshot: int | None = None):
    """Yield (query, partition, SQL); cleanup staged files on every exit.

    Hash partitioning preserves equal content identities, including duplicates.
    Each referenced relation is staged in a single scan with only columns used by
    the checks. No per-partition lake scan or occurrence-wide global join remains.
    """
    if partitions < 1:
        raise ValueError("validation partitions must be positive")
    expanding = {i: sql for i, sql in enumerate(spec.validation_queries) if _expanding(sql)}
    columns: dict[str, set[str]] = {}
    registered = {p.name: p for p in PROJECTIONS}
    for sql in expanding.values():
        tree, tables = _sources(sql, spec)
        used = {column.name for column in tree.find_all(exp.Column)} | {'content_sha256'}
        for table in tables:
            available = {column.name for column in registered[table.name].columns}
            columns.setdefault(table.name, set()).update(used & available)
    with TemporaryDirectory(prefix='periplus-validation-') as directory:
        paths: dict[tuple[str, int], list[str]] = {}
        for name, selected in columns.items():
            destination = Path(directory) / name
            projection = ', '.join(f'"{column}"' for column in sorted(selected))
            event('materialization_validation_staging', operation=name, outcome='started')
            source = at_snapshot(
                f"SELECT {projection}, hash(content_sha256) % {partitions} "
                f"AS __validation_partition FROM material.{name}", snapshot
            )
            catalogue.trusted_remote_execute(
                f"COPY ({source}) TO {sql_string(str(destination))} "
                "(FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (__validation_partition))"
            )
            for path in destination.rglob('*.parquet'):
                partition = int(path.parent.name.split('=')[1])
                paths.setdefault((name, partition), []).append(str(path))
            event('materialization_validation_staging', operation=name, outcome='finished',
                  bytes=sum(path.stat().st_size for path in destination.rglob('*.parquet')))

        def statements() -> Iterator[tuple[int, int, str]]:
            for index, sql in enumerate(spec.validation_queries):
                if index not in expanding:
                    yield index, 0, at_snapshot(sql, snapshot)
                    continue
                for partition in range(partitions):
                    tree, tables = _sources(sql, spec)
                    for table in tables:
                        files = paths.get((table.name, partition), [])
                        selected = ', '.join(f'"{column}"' for column in sorted(columns[table.name]))
                        source = (f"read_parquet([{','.join(sql_string(path) for path in files)}], hive_partitioning=false)"
                                  if files else f'material.{table.name}')
                        replacement = parse_one(
                            f'SELECT {selected} FROM {source}' + ('' if files else ' WHERE false'), read='duckdb'
                        ).subquery(alias=table.alias_or_name)
                        table.replace(replacement)
                    yield index, partition, tree.sql(dialect='duckdb')
        yield statements()
