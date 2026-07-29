"""Remote DuckLake connection and logical schema boundary."""

from __future__ import annotations

import logging
import hashlib
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager

import duckdb
import pyarrow as pa

from atlas.platform.catalogue.config import CatalogueConfig
from atlas.platform.catalogue.bulk import (
    BulkCatalogueAction,
    BulkCommitFile,
    BulkCommitOperation,
    DuckBasinBulkClient,
)
from atlas.platform.catalogue.duckbasin import (
    DuckBasinClientMinter,
    DuckBasinCredentialRejectedError,
    MintedDuckDB,
    classify_quack_connection_error,
)
from atlas.platform.catalogue.exceptions import CatalogueSchemaError
from atlas.platform.catalogue.schema import (
    COLUMN_COMMENTS,
    INGEST_SCHEMA,
    MATERIAL_SCHEMA,
    TABLE_COMMENTS,
    TABLE_LAYOUTS,
    RelationName,
    expected_columns,
)
from atlas.platform.catalogue.public import (
    install_public_catalogue,
    validate_public_catalogue,
)

_INTERNAL_TABLE_NAME = re.compile(r"^_atlas_[a-z0-9_]+$")


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
        self._connection_generation = 1
        self._remint_count = 0
        self._remote_transaction_active = False
        self._connection_remint_required = False
        self._bulk_client: DuckBasinBulkClient | None = None
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

    @property
    def connection_generation(self) -> int:
        return self._connection_generation

    @property
    def remint_count(self) -> int:
        return self._remint_count

    @property
    def token_status(self) -> tuple[str, int]:
        return self._minter.token_status()

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
        except BaseException as operation_error:
            try:
                self.trusted_remote_execute("ROLLBACK")
            except BaseException as rollback_error:
                self._connection_remint_required = True
                operation_error.add_note(
                    "The remote transaction rollback also failed: "
                    f"{type(rollback_error).__name__}: {rollback_error}"
                )
                logging.warning(
                    "remote transaction rollback failed after %s",
                    type(operation_error).__name__,
                    exc_info=rollback_error,
                )
            finally:
                self._remote_transaction_active = False
            raise
        else:
            try:
                self.trusted_remote_execute("COMMIT")
            except BaseException:
                # A failed or unacknowledged COMMIT can leave the Quack
                # session inside the old transaction. Never retry BEGIN on
                # that session: close/remint it before the next operation.
                self._connection_remint_required = True
                raise
            finally:
                self._remote_transaction_active = False

    def bootstrap(self) -> None:
        schemas = (INGEST_SCHEMA, MATERIAL_SCHEMA)
        alias = _quote_literal(self.config.alias)
        existing_table_comments = {
            (str(schema_name), str(table_name)): comment
            for schema_name, table_name, comment in self.trusted_remote_rows(
                "SELECT schema_name, table_name, comment FROM duckdb_tables() "
                f"WHERE database_name = {alias} "
                f"AND schema_name IN ('{INGEST_SCHEMA}', '{MATERIAL_SCHEMA}')"
            )
        }
        existing_column_comments = {
            (str(schema_name), str(table_name), str(column_name)): comment
            for schema_name, table_name, column_name, comment
            in self.trusted_remote_rows(
                "SELECT schema_name, table_name, column_name, comment "
                "FROM duckdb_columns() "
                f"WHERE database_name = {alias} "
                f"AND schema_name IN ('{INGEST_SCHEMA}', '{MATERIAL_SCHEMA}')"
            )
        }
        with self.remote_transaction():
            for schema in schemas:
                self.trusted_remote_execute(
                    "CREATE SCHEMA IF NOT EXISTS "
                    f"{_qualified(self.config.alias, schema)}"
                )
            for relation_name, columns in expected_columns().items():
                relation = _qualified(
                    self.config.alias,
                    relation_name.schema,
                    relation_name.table,
                )
                definitions = ", ".join(
                    f"{_quote_identifier(name)} {_column_type(column)}"
                    + ("" if column.nullable else " NOT NULL")
                    for name, column in columns.items()
                )
                self.trusted_remote_execute(
                    f"CREATE TABLE IF NOT EXISTS {relation} ({definitions})"
                )
                identity = (relation_name.schema, relation_name.table)
                if identity not in existing_table_comments:
                    layout = TABLE_LAYOUTS[relation_name]
                    if layout.partition_by:
                        self.trusted_remote_execute(
                            f"ALTER TABLE {relation} SET PARTITIONED BY "
                            f"({', '.join(layout.partition_by)})"
                        )
                    if layout.sort_by:
                        self.trusted_remote_execute(
                            f"ALTER TABLE {relation} SET SORTED BY "
                            f"({', '.join(layout.sort_by)})"
                        )
                if (
                    existing_table_comments.get(identity)
                    != TABLE_COMMENTS[relation_name]
                ):
                    self.trusted_remote_execute(
                        f"COMMENT ON TABLE {relation} IS "
                        f"{_quote_literal(TABLE_COMMENTS[relation_name])}"
                    )
                for column_name, comment in COLUMN_COMMENTS[relation_name].items():
                    if (
                        existing_column_comments.get(
                            (
                                relation_name.schema,
                                relation_name.table,
                                column_name,
                            )
                        )
                        != comment
                    ):
                        self.trusted_remote_execute(
                            f"COMMENT ON COLUMN {relation}."
                            f"{_quote_identifier(column_name)} IS "
                            f"{_quote_literal(comment)}"
                        )
        install_public_catalogue(self)
        self._use_schema_if_available()
        self.validate_schema()

    def validate_schema(self) -> None:
        errors: list[str] = []
        for relation_name, expected in expected_columns().items():
            relation = _qualified(
                self.config.alias,
                relation_name.schema,
                relation_name.table,
            )
            try:
                rows = self.trusted_remote_rows(f"DESCRIBE {relation}")
            except Exception as exc:
                errors.append(f"{relation_name.qualified}: {exc}")
                continue
            actual = {
                str(row[0]): (
                    _normalize_type(str(row[1])),
                    str(row[2]).upper() == "YES",
                )
                for row in rows
            }
            wanted = {
                name: (
                    _normalize_type(_column_type(column)),
                    column.nullable,
                )
                for name, column in expected.items()
            }
            if actual != wanted:
                errors.append(
                    f"{relation_name.qualified}: expected {wanted}, got {actual}"
                )
        alias = _quote_literal(self.config.alias)
        try:
            table_comment_rows = self.trusted_remote_rows(
                "SELECT schema_name, table_name, comment FROM duckdb_tables() "
                f"WHERE database_name = {alias} "
                f"AND schema_name IN ('{INGEST_SCHEMA}', '{MATERIAL_SCHEMA}')"
            )
            column_comment_rows = self.trusted_remote_rows(
                "SELECT schema_name, table_name, column_name, comment "
                "FROM duckdb_columns() "
                f"WHERE database_name = {alias} "
                f"AND schema_name IN ('{INGEST_SCHEMA}', '{MATERIAL_SCHEMA}')"
            )
        except Exception as exc:
            errors.append(f"catalogue comments: {exc}")
        else:
            actual_table_comments = {
                (str(schema_name), str(table_name)): comment
                for schema_name, table_name, comment in table_comment_rows
            }
            actual_column_comments = {
                (str(schema_name), str(table_name), str(column_name)): comment
                for schema_name, table_name, column_name, comment
                in column_comment_rows
            }
            for relation_name in expected_columns():
                identity = (relation_name.schema, relation_name.table)
                expected_table_comment = TABLE_COMMENTS[relation_name]
                if actual_table_comments.get(identity) != expected_table_comment:
                    errors.append(
                        f"{relation_name.qualified}: missing or stale table comment"
                    )
                for column_name, expected_comment in COLUMN_COMMENTS[
                    relation_name
                ].items():
                    if (
                        actual_column_comments.get(
                            (
                                relation_name.schema,
                                relation_name.table,
                                column_name,
                            )
                        )
                        != expected_comment
                    ):
                        errors.append(
                            f"{relation_name.qualified}.{column_name}: "
                            "missing or stale column comment"
                        )
        if errors:
            raise CatalogueSchemaError("; ".join(errors))
        validate_public_catalogue(self)

    def create_materialization_generations(
        self,
        generations: Mapping[RelationName, str],
        *,
        generation_id: str,
    ) -> None:
        """Atomically create empty, fully typed rebuild destinations."""

        if not generations:
            raise ValueError("at least one generation is required")
        suffix = re.sub(r"[^a-z0-9]", "", generation_id.lower())[:32]
        if not suffix:
            raise ValueError("generation_id must contain letters or digits")
        for relation_name, generation_table in generations.items():
            if relation_name.schema != MATERIAL_SCHEMA:
                raise ValueError(
                    "only material tables can have rebuild generations"
                )
            if not _INTERNAL_TABLE_NAME.fullmatch(generation_table):
                raise ValueError("invalid internal generation table name")
        entries = tuple(
            {
                "schema": relation_name.schema,
                "source_table": relation_name.table,
                "target_table": generation_table,
            }
            for relation_name, generation_table in sorted(
                generations.items(),
                key=lambda item: item[0].qualified,
            )
        )
        self.commit_catalogue_actions(
            (
                BulkCatalogueAction(
                    action_id="clone-generations",
                    kind="clone_tables",
                    entries=entries,
                ),
            ),
            idempotency_key=(
                f"atlas:generation:clone:v1:{suffix}"
            ),
        )

    def activate_materialization_generations(
        self,
        generations: Mapping[RelationName, str],
        *,
        activation_id: str,
    ) -> None:
        """Atomically replace selected public material tables."""

        if not generations:
            raise ValueError("at least one generation is required")
        suffix = re.sub(r"[^a-z0-9]", "", activation_id.lower())[:20]
        if not suffix:
            raise ValueError("activation_id must contain letters or digits")
        for relation_name, generation_table in generations.items():
            if relation_name.schema != MATERIAL_SCHEMA:
                raise ValueError("only material tables can be activated")
            if not _INTERNAL_TABLE_NAME.fullmatch(generation_table):
                raise ValueError("invalid internal generation table name")
        entries = tuple(
            {
                "schema": relation_name.schema,
                "current_table": relation_name.table,
                "generation_table": generation_table,
                "retired_table": (
                    f"_atlas_retired_{relation_name.table}_{suffix}"
                ),
            }
            for relation_name, generation_table in sorted(
                generations.items(),
                key=lambda item: item[0].qualified,
            )
        )
        self.commit_catalogue_actions(
            (
                BulkCatalogueAction(
                    action_id="activate-generations",
                    kind="swap_tables",
                    entries=entries,
                ),
            ),
            idempotency_key=(
                f"atlas:generation:activate:v1:{activation_id}"
            ),
        )

    def finalize_materialization_activation(
        self,
        relations,
        *,
        activation_id: str,
    ) -> None:
        """Remove replay markers after control state records completion."""

        suffix = re.sub(r"[^a-z0-9]", "", activation_id.lower())[:20]
        if not suffix:
            raise ValueError("activation_id must contain letters or digits")
        selected = tuple(relations)
        if not selected:
            return
        entries = []
        for relation_name in selected:
            if relation_name.schema != MATERIAL_SCHEMA:
                raise ValueError(
                    "only material tables can be finalized"
                )
            entries.append(
                {
                    "schema": relation_name.schema,
                    "table": (
                        f"_atlas_retired_{relation_name.table}_{suffix}"
                    ),
                }
            )
        self.commit_catalogue_actions(
            (
                BulkCatalogueAction(
                    action_id="drop-retired-generations",
                    kind="drop_tables",
                    entries=tuple(entries),
                ),
            ),
            idempotency_key=(
                f"atlas:generation:finalize:v1:{activation_id}"
            ),
        )

    def drop_materialization_generations(
        self,
        generation_tables,
    ) -> None:
        """Drop failed rebuild tables without touching public relations."""

        tables = tuple(generation_tables)
        for table in tables:
            if not _INTERNAL_TABLE_NAME.fullmatch(table):
                raise ValueError("invalid internal generation table name")
        if not tables:
            return
        digest = hashlib.sha256(
            "\n".join(sorted(tables)).encode()
        ).hexdigest()
        self.commit_catalogue_actions(
            (
                BulkCatalogueAction(
                    action_id="drop-failed-generations",
                    kind="drop_tables",
                    entries=tuple(
                        {
                            "schema": MATERIAL_SCHEMA,
                            "table": table,
                        }
                        for table in sorted(tables)
                    ),
                ),
            ),
            idempotency_key=(
                f"atlas:generation:drop:v1:{digest}"
            ),
        )

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

    def trusted_remote_result(
        self,
        sql: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...], list[tuple]]:
        """Execute trusted remote SQL and retain its result-column metadata."""

        cursor = self._execute_remote(
            "FROM quack_query_by_name(current_catalog(), ?)",
            sql,
        )
        columns = tuple(str(item[0]) for item in cursor.description)
        types = tuple(str(item[1]) for item in cursor.description)
        return columns, types, cursor.fetchall()

    def trusted_remote_execute(self, sql: str) -> list[tuple]:
        """Execute trusted Atlas SQL in the session-affine Basin process."""

        return self._execute_remote(
            "CALL quack_query_by_name(current_catalog(), ?)",
            sql,
        ).fetchall()

    def append(
        self,
        table_name: str,
        rows: list[dict[str, object]],
        *,
        schema_name: str,
    ) -> None:
        if not rows:
            return
        self.append_arrow(
            table_name,
            pa.Table.from_pylist(rows),
            schema_name=schema_name,
        )

    def append_arrow(
        self,
        table_name: str,
        table: pa.Table,
        *,
        schema_name: str,
        variant_columns: frozenset[str] = frozenset(),
    ) -> None:
        """Stream one typed local Arrow relation into a managed table."""

        if table.num_rows == 0:
            return
        registration = f"_atlas_upload_{id(table):x}"
        self.trusted_connection.register(registration, table)
        try:
            relation = _qualified(
                schema_name,
                table_name,
            )
            projections = ", ".join(
                (
                    f"{_quote_identifier(name)}::JSON::VARIANT "
                    f"AS {_quote_identifier(name)}"
                    if name in variant_columns
                    else _quote_identifier(name)
                )
                for name in table.column_names
            )
            self.trusted_connection.execute(
                f"INSERT INTO {relation} BY NAME "
                f"SELECT {projections} "
                f"FROM {_quote_identifier(registration)}"
            )
        finally:
            self.trusted_connection.unregister(registration)

    def commit_bulk_files(
        self,
        files: Sequence[BulkCommitFile],
        *,
        idempotency_key: str,
    ) -> BulkCommitOperation:
        """Commit local Parquet files through Basin rather than Quack."""

        if self._bulk_client is None:
            self._bulk_client = DuckBasinBulkClient(
                self._minter.config,
                self._minter.target(),
                self._minter.tokens,
            )
        return self._bulk_client.commit(
            files,
            idempotency_key=idempotency_key,
        )

    def commit_catalogue_actions(
        self,
        actions: Sequence[BulkCatalogueAction],
        *,
        idempotency_key: str,
    ) -> BulkCommitOperation:
        """Commit replay-safe catalogue actions through Basin."""

        if self._bulk_client is None:
            self._bulk_client = DuckBasinBulkClient(
                self._minter.config,
                self._minter.target(),
                self._minter.tokens,
            )
        return self._bulk_client.commit(
            idempotency_key=idempotency_key,
            actions=actions,
        )

    def close(self) -> None:
        try:
            if self._bulk_client is not None:
                self._bulk_client.close()
        finally:
            try:
                self._minted.close()
            finally:
                self._minter.close()

    def refresh_metadata(self) -> None:
        """Mint a fresh session after Atlas creates internal rebuild tables."""

        self._remint_connection(self._minted)

    def __enter__(self) -> Catalogue:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _use_schema_if_available(self, connection=None) -> None:
        active_connection = connection or self.trusted_connection
        namespace = _qualified(self.config.alias, INGEST_SCHEMA)
        try:
            active_connection.execute(f"USE {namespace}")
        except Exception:
            # A newly provisioned lake has no Atlas schema until bootstrap.
            active_connection.execute(
                f"USE {_quote_identifier(self.config.alias)}"
            )

    def _execute_remote(self, local_sql: str, remote_sql: str):
        return self._execute_local(local_sql, [remote_sql])

    def _ensure_fresh_connection(self) -> None:
        failed_minted = self._minted
        if (
            not self._remote_transaction_active
            and (
                self._connection_remint_required
                or self._minter.connection_credentials_stale(failed_minted)
            )
        ):
            self._remint_connection(failed_minted)

    def _execute_local(self, sql: str, parameters=None):
        self._ensure_fresh_connection()
        failed_minted = self._minted
        try:
            return failed_minted.connection.execute(sql, parameters)
        except duckdb.Error as exc:
            classified = classify_quack_connection_error(exc)
            if classified is None:
                raise
            if self._remote_transaction_active:
                if isinstance(
                    classified,
                    DuckBasinCredentialRejectedError,
                ):
                    self._minter.invalidate_connection_credentials(
                        failed_minted
                    )
                raise classified from exc
            if isinstance(classified, DuckBasinCredentialRejectedError):
                self._minter.invalidate_connection_credentials(failed_minted)
        self._remint_connection(failed_minted)
        try:
            return self._minted.connection.execute(sql, parameters)
        except duckdb.Error as exc:
            classified = classify_quack_connection_error(exc)
            if classified is not None:
                if isinstance(
                    classified,
                    DuckBasinCredentialRejectedError,
                ):
                    self._minter.invalidate_connection_credentials(
                        self._minted
                    )
                raise classified from exc
            raise

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
                # Configure the replacement directly. Going through the
                # resilient facade here would observe the pending remint flag
                # and recursively try to acquire _remint_lock.
                self._use_schema_if_available(replacement.connection)
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
            self._connection_generation += 1
            self._remint_count += 1
            self._connection_remint_required = False


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
