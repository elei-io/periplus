"""Temporary Quack 1.5 bridge for an attached server-side DuckLake.

Delete this module when Quack can attach a selected remote catalogue directly.
"""

from __future__ import annotations

import math
import hashlib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import TracebackType
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import duckdb
from sqlglot import exp, parse_one

from config import get_bool, get_optional, get_str
from repository.catalogue.exceptions import CatalogueSchemaError
from repository.objects.config import object_store_from_env


@dataclass(frozen=True)
class QuackCatalogueConfig:
    """Only the logical names a remote client needs to address the lake."""

    alias: str
    schema: str


class QuackCatalogue:
    """Catalogue-shaped client whose SQL executes in the state-layer DuckDB."""

    def __init__(self, config: QuackCatalogueConfig) -> None:
        self.config = config
        self._store = object_store_from_env()
        self._staged_keys: set[str] = set()
        self.connection = QuackConnection(
            uri=get_str("ATLAS_QUACK_URL"),
            token=get_str("ATLAS_QUACK_CLIENT_TOKEN"),
            disable_ssl=get_bool("ATLAS_QUACK_DISABLE_SSL"),
            catalogue_alias=config.alias,
            stage_path=self._stage_path,
            cleanup_staging=self._cleanup_staging,
        )
        self.lake = _RemoteLake(self)

    def validate_schema(self) -> None:
        for table in ("documents", "crawls", "elements", "materialization_scope_results"):
            try:
                self.connection.execute(
                    f"SELECT * FROM {_qualified(self.config, table)} LIMIT 0"
                )
            except duckdb.Error as exc:
                raise CatalogueSchemaError(
                    f"remote DuckLake table is missing or unreadable: {table}"
                ) from exc

    @property
    def metadata_schema(self) -> str:
        """Metadata schema exposed by Atlas's Postgres-backed Quack endpoint."""

        return "public"

    def latest_snapshot(self) -> int | None:
        row = self.connection.execute(
            f"SELECT snapshot_id FROM {_ident(self.config.alias)}.snapshots() "
            "ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
        return None if row is None else int(row[0])

    def last_committed_snapshot(self) -> int | None:
        return self.connection.last_committed_snapshot

    def set_commit_message(
        self,
        *,
        author: str,
        message: str,
        extra: dict[str, object] | None = None,
    ) -> None:
        import json

        self.connection.execute(
            f"CALL {_ident(self.config.alias)}.set_commit_message(?, ?, extra_info => ?)",
            [author, message, json.dumps(extra or {}, separators=(",", ":"))],
        )

    def close(self) -> None:
        self.connection.close()

    def _stage_path(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as content:
            while chunk := content.read(1024 * 1024):
                digest.update(chunk)
        key = f"staging/quack/sha256/{digest.hexdigest()}/{path.name}"
        with path.open("rb") as content:
            self._store.put_if_absent(key, content)
        if self._store.size(key) != path.stat().st_size:
            raise RuntimeError(f"Quack staging object size mismatch: {key}")
        self._staged_keys.add(key)
        bucket = get_str("ATLAS_REPOSITORY_S3_BUCKET")
        prefix = (get_optional("ATLAS_REPOSITORY_S3_PREFIX") or "").strip("/")
        object_key = f"{prefix}/{key}" if prefix else key
        return f"s3://{bucket}/{object_key}"

    def _cleanup_staging(self) -> None:
        for key in self._staged_keys:
            self._store.delete(key)
        self._staged_keys.clear()

    def __enter__(self) -> QuackCatalogue:
        self.validate_schema()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class QuackConnection:
    """DuckDB-like connection over the official ``remote.query`` escape hatch."""

    def __init__(
        self,
        *,
        uri: str,
        token: str,
        disable_ssl: bool,
        catalogue_alias: str,
        stage_path,
        cleanup_staging,
    ) -> None:
        self._connection = duckdb.connect()
        self._connection.execute("LOAD quack")
        self._remote_alias = "atlas_quack_remote"
        self._connection.execute(
            f"ATTACH {_sql_literal(uri)} AS {_ident(self._remote_alias)} "
            f"(TYPE quack, TOKEN {_sql_literal(token)}, "
            f"DISABLE_SSL {'true' if disable_ssl else 'false'})"
        )
        self._catalogue_alias = catalogue_alias
        self._stage_path = stage_path
        self._cleanup_staging = cleanup_staging
        self._transaction: list[str] | None = None
        self.last_committed_snapshot: int | None = None

    def execute(
        self,
        sql: str,
        parameters: Sequence[object] | Mapping[str, object] | None = None,
    ):
        parameters = _stage_parameters(sql, parameters, self._stage_path)
        rendered = render_bound_sql(sql, parameters)
        if self._transaction is not None and _is_write(rendered):
            self._transaction.append(rendered)
            return _QueuedResult()
        return self._query(rendered)

    def _query(self, sql: str):
        return self._connection.execute(
            f"FROM {_ident(self._remote_alias)}.query(?)",
            [sql],
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._transaction is not None:
            raise RuntimeError("nested Quack transactions are not supported")
        self._transaction = []
        try:
            yield
            statements = self._transaction
            if statements:
                script = ";\n".join(
                    [
                        "BEGIN",
                        *statements,
                        "COMMIT",
                    ]
                )
                self._query(script).fetchall()
                # Quack preserves the logical server connection across requests.
                # Keep snapshot attribution on that committing connection; do not
                # substitute the globally latest DuckLake snapshot.
                row = self._query(
                    f"SELECT id FROM {_ident(self._catalogue_alias)}.last_committed_snapshot()"
                ).fetchone()
                self.last_committed_snapshot = (
                    None if row is None or row[0] is None else int(row[0])
                )
        finally:
            self._transaction = None
            self._cleanup_staging()

    def close(self) -> None:
        self._cleanup_staging()
        self._connection.close()


class _RemoteLake:
    def __init__(self, catalogue: QuackCatalogue) -> None:
        self.catalogue = catalogue
        self.alias = catalogue.config.alias
        self.connection = catalogue.connection
        self.table = _RemoteTables(catalogue)
        self.snapshots = _RemoteSnapshots(catalogue)

    def transaction(self):
        return self.connection.transaction()

    @contextmanager
    def fence(self, *_args, **_kwargs) -> Iterator[None]:
        # Atlas has one repository writer. The remote transaction is the commit fence.
        yield

    def sql_dicts(self, sql: str, **parameters: object) -> list[dict[str, Any]]:
        cursor = self.connection.execute(sql, parameters)
        names = [str(column[0]) for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def sql_scalar(self, sql: str, **parameters: object) -> object | None:
        row = self.connection.execute(sql, parameters).fetchone()
        return None if row is None else row[0]


class _RemoteTables:
    def __init__(self, catalogue: QuackCatalogue) -> None:
        self.catalogue = catalogue

    def append(
        self,
        table_name: str,
        rows: list[dict[str, object]],
        *,
        schema_name: str,
    ) -> None:
        if not rows:
            return
        columns = list(rows[0])
        if any(list(row) != columns for row in rows):
            raise ValueError("Quack append rows must have identical columns")
        values = ", ".join(
            "(" + ", ".join(_sql_literal(row[column]) for column in columns) + ")"
            for row in rows
        )
        column_sql = ", ".join(_ident(column) for column in columns)
        table = ".".join(
            _ident(part)
            for part in (self.catalogue.config.alias, schema_name, table_name)
        )
        self.catalogue.connection.execute(
            f"INSERT INTO {table} BY NAME SELECT * FROM (VALUES {values}) "
            f"AS staged({column_sql})"
        )

    def list(self, *, schema_name: str):
        rows = self.catalogue.connection.execute(
            "SELECT table_name FROM duckdb_tables() "
            "WHERE database_name = ? AND schema_name = ? ORDER BY table_name",
            [self.catalogue.config.alias, schema_name],
        ).fetchall()
        return [SimpleNamespace(table_name=str(row[0])) for row in rows]

    def info(
        self,
        table_name: str,
        *,
        schema_name: str,
        include_summary: bool = False,
        include_row_count: bool = False,
        include_snapshots: bool = False,
    ):
        del include_summary, include_snapshots
        table = ".".join(
            _ident(part)
            for part in (self.catalogue.config.alias, schema_name, table_name)
        )
        rows = self.catalogue.connection.execute(f"DESCRIBE {table}").fetchall()
        if not rows:
            raise RuntimeError(f"remote table is unavailable: {schema_name}.{table_name}")
        columns = [
            SimpleNamespace(
                name=str(row[0]),
                data_type=str(row[1]),
                nullable=str(row[2]).upper() != "NO",
            )
            for row in rows
        ]
        row_count = None
        if include_row_count:
            row_count = int(
                self.catalogue.connection.execute(
                    f"SELECT count(*) FROM {table}"
                ).fetchone()[0]
            )
        return SimpleNamespace(columns=columns, row_count=row_count)


class _RemoteSnapshots:
    def __init__(self, catalogue: QuackCatalogue) -> None:
        self.catalogue = catalogue

    def latest(self) -> int | None:
        return self.catalogue.latest_snapshot()


class _QueuedResult:
    description: list[tuple[object, ...]] = []

    def fetchone(self):
        return None

    def fetchall(self) -> list[object]:
        return []


def render_bound_sql(
    sql: str,
    parameters: Sequence[object] | Mapping[str, object] | None,
) -> str:
    """Bind values through SQLGlot's AST; never interpolate raw application text."""

    if sql.lstrip().upper().startswith("CALL "):
        if isinstance(parameters, Mapping):
            raise ValueError("CALL statements require positional parameters")
        return _render_positional_command(sql, list(parameters or ()))
    # SQLGlot does not yet parse DuckDB's AT (VERSION => ...) table suffix.
    # Bind this Atlas-owned form with the same typed literal encoder used for
    # CALL statements; the server remains the syntax authority for this clause.
    if "AT (VERSION => ?)" in sql.upper():
        if isinstance(parameters, Mapping):
            raise ValueError("time-travel statements require positional parameters")
        return _render_positional_command(sql, list(parameters or ()))
    # SQLGlot currently truncates DuckLake's partition DDL to ``ALTER ... SET``.
    # This statement is generated entirely from validated/quoted Atlas identifiers
    # and contains no application values, so preserve it for the server parser.
    if " SET PARTITIONED BY " in sql.upper():
        if parameters:
            raise ValueError("partition statements do not accept parameters")
        return sql
    tree = parse_one(sql, read="duckdb")
    positional = iter(parameters or ()) if not isinstance(parameters, Mapping) else None
    named = parameters if isinstance(parameters, Mapping) else None
    def replace(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Placeholder):
            return node
        name = str(node.this) if node.this is not None else ""
        if name:
            if named is None or name not in named:
                raise ValueError(f"missing SQL parameter: {name}")
            value = named[name]
        else:
            if positional is None:
                raise ValueError("positional placeholder used with named parameters")
            try:
                value = next(positional)
            except StopIteration as exc:
                raise ValueError("not enough SQL parameters") from exc
        return parse_one(_sql_literal(value), read="duckdb")

    rendered = tree.transform(replace).sql(dialect="duckdb")
    if positional is not None:
        try:
            next(positional)
        except StopIteration:
            pass
        else:
            raise ValueError("too many SQL parameters")
    if named is not None:
        placeholder_names = {
            str(node.this)
            for node in tree.walk()
            if isinstance(node, exp.Placeholder) and node.this is not None
        }
        extras = set(named) - placeholder_names
        if extras:
            raise ValueError(f"unused SQL parameters: {', '.join(sorted(extras))}")
    return rendered


def _render_positional_command(sql: str, parameters: list[object]) -> str:
    """Bind placeholders in Atlas-owned CALL templates while respecting SQL strings."""

    output: list[str] = []
    index = 0
    parameter_index = 0
    quoted = False
    while index < len(sql):
        char = sql[index]
        if char == "'":
            output.append(char)
            if quoted and index + 1 < len(sql) and sql[index + 1] == "'":
                output.append("'")
                index += 2
                continue
            quoted = not quoted
        elif char == "?" and not quoted:
            if parameter_index >= len(parameters):
                raise ValueError("not enough SQL parameters")
            output.append(_sql_literal(parameters[parameter_index]))
            parameter_index += 1
        else:
            output.append(char)
        index += 1
    if quoted:
        raise ValueError("unterminated SQL string")
    if parameter_index != len(parameters):
        raise ValueError("too many SQL parameters")
    return "".join(output)


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite SQL float is not supported")
        return repr(value)
    if isinstance(value, UUID):
        return f"CAST({exp.Literal.string(str(value)).sql()} AS UUID)"
    if isinstance(value, datetime):
        return f"CAST({exp.Literal.string(value.isoformat()).sql()} AS TIMESTAMPTZ)"
    if isinstance(value, date):
        return f"CAST({exp.Literal.string(value.isoformat()).sql()} AS DATE)"
    if isinstance(value, str):
        return exp.Literal.string(value).sql(dialect="duckdb")
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_sql_literal(item) for item in value) + "]"
    raise TypeError(f"unsupported Quack SQL parameter type: {type(value).__name__}")


def _stage_parameters(sql: str, parameters, stage_path):
    if parameters is None:
        return None

    def stage(value: object) -> object:
        if isinstance(value, Path):
            return stage_path(value)
        if (
            isinstance(value, str)
            and "read_parquet" in sql.lower()
            and not value.startswith(("s3://", "http://", "https://"))
        ):
            return stage_path(Path(value))
        if isinstance(value, list):
            return [stage(item) for item in value]
        if isinstance(value, tuple):
            return tuple(stage(item) for item in value)
        return value

    if isinstance(parameters, Mapping):
        return {name: stage(value) for name, value in parameters.items()}
    return [stage(value) for value in parameters]


def _is_write(sql: str) -> bool:
    if sql.lstrip().upper().startswith("CALL "):
        return True
    statement = parse_one(sql, read="duckdb")
    return isinstance(
        statement,
        (exp.Insert, exp.Delete, exp.Update, exp.Create, exp.Drop, exp.Alter, exp.Command),
    )


def _qualified(config: QuackCatalogueConfig, table: str) -> str:
    return ".".join(_ident(part) for part in (config.alias, config.schema, table))


def _ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quack_catalogue_from_env() -> QuackCatalogue:
    return QuackCatalogue(
        QuackCatalogueConfig(
            alias=get_str("ATLAS_CATALOGUE_ALIAS"),
            schema=get_str("ATLAS_CATALOGUE_SCHEMA"),
        )
    )
