"""Remote DuckLake connection and logical schema boundary."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
import logging
import threading
from typing import Any

import duckdb
import pyarrow as pa

from repository.catalogue.config import CatalogueConfig
from repository.catalogue.duckbasin import (
    DuckBasinClientMinter,
    MintedDuckDB,
    is_quack_authorization_error,
    is_recoverable_quack_connection_error,
)
from repository.catalogue.exceptions import CatalogueSchemaError
from repository.catalogue.schema import (
    INTERNAL_SCHEMA,
    expected_columns,
)


class Catalogue:
    """One session-affine connection to a DuckBasin-managed DuckLake."""

    def __init__(
        self,
        config: CatalogueConfig,
        *,
        minted: MintedDuckDB,
        minter: DuckBasinClientMinter,
        duckdb_config: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self._minted = minted
        self._minter = minter
        self._duckdb_config = dict(duckdb_config or {})
        self._remint_lock = threading.Lock()
        self._remote_transaction_active = False
        self._connection_proxy = _ResilientDuckDBConnection(self)
        if minted.catalogue_alias != config.alias:
            raise ValueError(
                "DuckBasin catalogue alias does not match Atlas configuration"
            )
        self._use_schema_if_available()

    @property
    def trusted_connection(self):
        """Return the raw local connection for trusted Atlas infrastructure SQL."""

        return self._connection_proxy

    @property
    def session_id(self) -> str:
        return self._minted.session_id

    @property
    def lake_slug(self) -> str:
        return self._minted.lake_slug

    @contextmanager
    def transaction(self) -> Iterator[Catalogue]:
        """One Basin transaction shared by remote SQL and attached transfers."""

        with self.remote_transaction():
            yield self

    @contextmanager
    def remote_transaction(self) -> Iterator[Catalogue]:
        """Transaction executed by the session-affine Basin DuckDB process."""

        self.trusted_remote_execute("BEGIN TRANSACTION")
        self._remote_transaction_active = True
        try:
            yield self
        except BaseException:
            try:
                self.trusted_remote_execute("ROLLBACK")
            finally:
                self._remote_transaction_active = False
            raise
        else:
            try:
                self.trusted_remote_execute("COMMIT")
            finally:
                self._remote_transaction_active = False

    def bootstrap(self) -> None:
        schemas = (
            self.config.schema,
            "views",
            "macros",
            INTERNAL_SCHEMA,
            "_atlas_materializations",
        )
        with self.remote_transaction():
            for schema in schemas:
                self.trusted_remote_execute(
                    "CREATE SCHEMA IF NOT EXISTS "
                    f"{_qualified(self.config.alias, schema)}"
                )
            for table_name, columns in expected_columns().items():
                definitions = ", ".join(
                    f"{_quote_identifier(name)} {_column_type(column)}"
                    + ("" if column.nullable else " NOT NULL")
                    for name, column in columns.items()
                )
                self.trusted_remote_execute(
                    "CREATE TABLE IF NOT EXISTS "
                    f"{_qualified(self.config.alias, self.config.schema, table_name)} "
                    f"({definitions})"
                )
        self._use_schema_if_available()
        self.validate_schema()

    def validate_schema(self) -> None:
        errors: list[str] = []
        for table_name, expected in expected_columns().items():
            relation = _qualified(
                self.config.alias,
                self.config.schema,
                table_name,
            )
            try:
                rows = self.trusted_remote_rows(f"DESCRIBE {relation}")
            except Exception as exc:
                errors.append(f"{self.config.schema}.{table_name}: {exc}")
                continue
            actual = {
                str(row[0]): _normalize_type(str(row[1]))
                for row in rows
            }
            wanted = {
                name: _normalize_type(_column_type(column))
                for name, column in expected.items()
            }
            if actual != wanted:
                errors.append(
                    f"{self.config.schema}.{table_name}: expected {wanted}, got {actual}"
                )
        if errors:
            raise CatalogueSchemaError("; ".join(errors))

    def latest_snapshot(self) -> int | None:
        rows = self.trusted_remote_rows(
            "SELECT max(snapshot_id) "
            f"FROM ducklake_snapshots({_quote_literal(self.config.alias)})"
        )
        value = rows[0][0] if rows else None
        return int(value) if value is not None else None

    def last_committed_snapshot(self) -> int | None:
        """Return the snapshot committed most recently by this remote session."""

        rows = self.trusted_remote_rows(
            "SELECT id FROM "
            f"{_quote_identifier(self.config.alias)}.last_committed_snapshot()"
        )
        value = rows[0][0] if rows else None
        return int(value) if value is not None else None

    def trusted_remote_rows(
        self,
        sql: str,
        parameters: Sequence[object] | Mapping[str, object] | None = None,
    ) -> list[tuple]:
        if parameters is not None:
            raise ValueError(
                "trusted_remote_rows accepts already-bound server SQL only"
            )
        cursor = self._execute_remote(
            "FROM quack_query_by_name(current_catalog(), ?)",
            sql,
        )
        return cursor.fetchall()

    def trusted_remote_execute(self, sql: str) -> list[tuple]:
        """Execute trusted Atlas SQL in the session-affine Basin process."""

        return self._execute_remote(
            "CALL quack_query_by_name(current_catalog(), ?)",
            sql,
        ).fetchall()

    def trusted_sql_dicts(
        self,
        sql: str,
        parameters: Mapping[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute trusted Atlas SQL and return rows keyed by column name."""

        cursor = self.trusted_connection.execute(sql, dict(parameters or {}))
        names = [description[0] for description in cursor.description]
        return [
            dict(zip(names, row, strict=True))
            for row in cursor.fetchall()
        ]

    def append(
        self,
        table_name: str,
        rows: list[dict[str, object]],
        *,
        schema_name: str | None = None,
    ) -> None:
        if not rows:
            return
        registration = f"_atlas_upload_{id(rows):x}"
        self.trusted_connection.register(registration, pa.Table.from_pylist(rows))
        try:
            relation = _qualified(
                self.config.alias,
                schema_name or self.config.schema,
                table_name,
            )
            self.trusted_connection.execute(
                f"INSERT INTO {relation} BY NAME "
                f"SELECT * FROM {_quote_identifier(registration)}"
            )
        finally:
            self.trusted_connection.unregister(registration)

    def close(self) -> None:
        try:
            self._minted.close()
        finally:
            self._minter.close()

    def __enter__(self) -> Catalogue:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _use_schema_if_available(self) -> None:
        namespace = _qualified(self.config.alias, self.config.schema)
        try:
            self.trusted_connection.execute(f"USE {namespace}")
        except Exception:
            # A newly provisioned lake has no Atlas schema until bootstrap.
            self.trusted_connection.execute(
                f"USE {_quote_identifier(self.config.alias)}"
            )

    def _execute_remote(self, local_sql: str, remote_sql: str):
        return self._execute_local(local_sql, [remote_sql])

    def _ensure_fresh_connection(self) -> None:
        failed_minted = self._minted
        if (
            not self._remote_transaction_active
            and self._minter.connection_credentials_stale(failed_minted)
        ):
            self._remint_connection(failed_minted)

    def _execute_local(self, sql: str, parameters=None):
        self._ensure_fresh_connection()
        failed_minted = self._minted
        try:
            return failed_minted.connection.execute(sql, parameters)
        except duckdb.Error as exc:
            if (
                self._remote_transaction_active
                or not is_recoverable_quack_connection_error(exc)
            ):
                raise
            if is_quack_authorization_error(exc):
                self._minter.invalidate_connection_credentials(failed_minted)
        self._remint_connection(failed_minted)
        return self._minted.connection.execute(sql, parameters)

    def _remint_connection(self, failed: MintedDuckDB) -> None:
        """Replace a Quack connection with stale routing or credentials."""

        with self._remint_lock:
            if self._minted is not failed:
                return
            replacement = self._minter.mint(
                duckdb_config=self._duckdb_config or None
            )
            if replacement.catalogue_alias != self.config.alias:
                replacement.close()
                raise ValueError(
                    "DuckBasin catalogue alias changed while reminting"
                )
            self._minted = replacement
            try:
                self._use_schema_if_available()
            except BaseException:
                self._minted = failed
                replacement.close()
                raise
            try:
                failed.close()
            except Exception:
                logging.warning(
                    "failed to close expired DuckBasin connection",
                    exc_info=True,
                )


class _ResilientDuckDBConnection:
    """Stable connection facade that remints after Basin scale-to-zero."""

    def __init__(self, catalogue: Catalogue) -> None:
        self._catalogue = catalogue

    def execute(self, sql: str, parameters=None):
        return self._catalogue._execute_local(sql, parameters)

    def __getattr__(self, name: str):
        self._catalogue._ensure_fresh_connection()
        return getattr(self._catalogue._minted.connection, name)


def _column_type(column) -> str:
    data_type = column.data_type
    renderer = getattr(data_type, "sql", None)
    return str(renderer() if callable(renderer) else data_type)


def _normalize_type(value: str) -> str:
    normalized = value.upper().replace(" ", "")
    if normalized == "TIMESTAMPWITHTIMEZONE":
        return "TIMESTAMPTZ"
    return normalized


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _qualified(*parts: str) -> str:
    return ".".join(_quote_identifier(part) for part in parts)
