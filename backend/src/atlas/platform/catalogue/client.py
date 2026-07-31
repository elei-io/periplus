"""Direct DuckLake connection and logical schema boundary."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pyarrow as pa

from atlas.platform.catalogue.config import CatalogueConfig
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
_REQUIRED_ATLAS_NATIVE_FUNCTIONS = frozenset(
    {"atlas_dom_select_first", "atlas_dom_select_all"}
)


class Catalogue:
    """One process-local connection to Atlas's shared DuckLake."""

    def __init__(
        self,
        config: CatalogueConfig,
        *,
        duckdb_config: Mapping[str, str] | None = None,
        load_cdc: bool = False,
        read_only: bool = False,
        override_data_path: bool = False,
    ) -> None:
        self.config = config
        connection_config = dict(duckdb_config or {})
        connection_config["allow_unsigned_extensions"] = "true"
        self._connection = duckdb.connect(
            ":memory:",
            config=connection_config,
        )
        self._connection.execute("INSTALL ducklake")
        self._connection.execute("LOAD ducklake")
        if config.metadata_path.startswith("postgres:"):
            self._connection.execute("INSTALL postgres")
            self._connection.execute("LOAD postgres")
        self._connection.load_extension(
            str(config.resolved_extension_path())
        )
        if load_cdc:
            self._connection.load_extension(
                str(config.resolved_cdc_extension_path())
            )
            # Prewarm this handle before it touches an attached catalog.
            self._connection.execute("SELECT cdc_version()").fetchone()
        native_functions = {
            str(name)
            for (name,) in self._connection.execute(
                "SELECT DISTINCT function_name FROM duckdb_functions() "
                "WHERE function_name IN "
                "('atlas_dom_select_first', 'atlas_dom_select_all')"
            ).fetchall()
        }
        if native_functions != _REQUIRED_ATLAS_NATIVE_FUNCTIONS:
            missing = sorted(
                _REQUIRED_ATLAS_NATIVE_FUNCTIONS - native_functions
            )
            self._connection.close()
            raise CatalogueSchemaError(
                "Atlas DuckDB extension is missing required functions: "
                + ", ".join(missing)
            )
        if "://" not in config.data_path:
            Path(config.data_path).mkdir(parents=True, exist_ok=True)
        attach_options = [
            f"DATA_PATH {_quote_literal(config.data_path)}",
            f"METADATA_SCHEMA {_quote_literal(config.metadata_schema)}",
        ]
        if override_data_path:
            attach_options.append("OVERRIDE_DATA_PATH true")
        if read_only:
            attach_options.append("READ_ONLY")
        attach = (
            f"ATTACH {_quote_literal('ducklake:' + config.metadata_path)} "
            f"AS {_quote_identifier(config.alias)} "
            f"({', '.join(attach_options)})"
        )
        self._connection.execute(attach)
        self._use_schema_if_available()

    @property
    def trusted_connection(self):
        """Return the raw local connection for trusted Atlas infrastructure SQL."""

        return self._connection

    @contextmanager
    def transaction(self) -> Iterator[Catalogue]:
        """One DuckLake transaction shared by all batch writes."""

        with self.remote_transaction():
            yield self

    @contextmanager
    def remote_transaction(self) -> Iterator[Catalogue]:
        """One direct DuckLake transaction."""

        self.trusted_remote_execute("BEGIN TRANSACTION")
        try:
            yield self
        except BaseException as operation_error:
            try:
                self.trusted_remote_execute("ROLLBACK")
            except BaseException as rollback_error:
                operation_error.add_note(
                    "The DuckLake transaction rollback also failed: "
                    f"{type(rollback_error).__name__}: {rollback_error}"
                )
            raise
        else:
            self.trusted_remote_execute("COMMIT")

    def bootstrap(self) -> None:
        schemas = (INGEST_SCHEMA, MATERIAL_SCHEMA)
        alias = _quote_literal(self.config.alias)
        with self.remote_transaction():
            for schema in schemas:
                self.trusted_remote_execute(
                    "CREATE SCHEMA IF NOT EXISTS "
                    f"{_qualified(self.config.alias, schema)}"
                )
            self.trusted_remote_execute(
                "CREATE TABLE IF NOT EXISTS "
                f"{_qualified(self.config.alias, MATERIAL_SCHEMA, '_atlas_applied_batches')} "
                "(run_id UUID NOT NULL, batch_id UUID NOT NULL, "
                "source_snapshot BIGINT NOT NULL, source_items BIGINT NOT NULL, "
                "source_bytes BIGINT NOT NULL, output_rows BIGINT NOT NULL, "
                "output_bytes BIGINT NOT NULL, committed_at TIMESTAMPTZ NOT NULL)"
            )
            self.trusted_remote_execute(
                "CREATE TABLE IF NOT EXISTS "
                f"{_qualified(self.config.alias, MATERIAL_SCHEMA, '_atlas_materialization_state')} "
                "(generation_id UUID NOT NULL, covered_snapshot BIGINT NOT NULL, "
                "batch_size INTEGER NOT NULL, "
                "registry_digest VARCHAR NOT NULL, "
                "activated_at TIMESTAMPTZ NOT NULL)"
            )
        state_columns = {
            str(row[0])
            for row in self.trusted_remote_rows(
                "DESCRIBE material._atlas_materialization_state"
            )
        }
        if "registry_digest" not in state_columns:
            with self.remote_transaction():
                self.trusted_remote_execute(
                    "ALTER TABLE material._atlas_materialization_state "
                    "ADD COLUMN registry_digest VARCHAR"
                )

        from atlas.materialization.registry import REGISTRY_DIGEST

        active_rows = self.trusted_remote_rows(
            "SELECT registry_digest "
            "FROM material._atlas_materialization_state "
            "ORDER BY activated_at DESC LIMIT 1"
        )
        active_registry_matches = (
            not active_rows
            or str(active_rows[0][0] or "") == REGISTRY_DIGEST
        )
        existing_tables = {
            (str(schema_name), str(table_name))
            for schema_name, table_name in self.trusted_remote_rows(
                "SELECT schema_name, table_name FROM duckdb_tables() "
                f"WHERE database_name = {alias} "
                f"AND schema_name IN ('{INGEST_SCHEMA}', '{MATERIAL_SCHEMA}')"
            )
        }
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
            for relation_name, columns in expected_columns().items():
                identity = (relation_name.schema, relation_name.table)
                if (
                    relation_name.schema == MATERIAL_SCHEMA
                    and identity in existing_tables
                    and not active_registry_matches
                ):
                    # Keep the old active relation readable while the new
                    # registry builds its hidden replacement.
                    continue
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
        if active_registry_matches:
            install_public_catalogue(self)
        self._use_schema_if_available()
        self.validate_schema(include_material=active_registry_matches)

    def validate_schema(
        self,
        *,
        include_material: bool | None = None,
    ) -> None:
        if include_material is None:
            include_material = self._active_registry_matches()
        errors: list[str] = []
        for relation_name, expected in expected_columns().items():
            if (
                relation_name.schema == MATERIAL_SCHEMA
                and not include_material
            ):
                continue
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
                if (
                    relation_name.schema == MATERIAL_SCHEMA
                    and not include_material
                ):
                    continue
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
        if include_material:
            validate_public_catalogue(self)

    def _active_registry_matches(self) -> bool:
        """Return whether active material state belongs to this deployment."""

        from atlas.materialization.registry import REGISTRY_DIGEST

        try:
            columns = {
                str(row[0])
                for row in self.trusted_remote_rows(
                    "DESCRIBE material._atlas_materialization_state"
                )
            }
            if "registry_digest" not in columns:
                return False
            rows = self.trusted_remote_rows(
                "SELECT registry_digest "
                "FROM material._atlas_materialization_state "
                "ORDER BY activated_at DESC LIMIT 1"
            )
        except Exception:
            # Setup owns reconciliation. Preserve strict validation for
            # fresh/test catalogues that do not have generation state.
            return True
        return not rows or str(rows[0][0] or "") == REGISTRY_DIGEST

    def create_materialization_generation(
        self,
        relation_name: RelationName,
        generation_table: str,
    ) -> None:
        """Create one empty, fully typed rebuild destination."""

        if relation_name.schema != MATERIAL_SCHEMA:
            raise ValueError("only material tables can have rebuild generations")
        if not _INTERNAL_TABLE_NAME.fullmatch(generation_table):
            raise ValueError("invalid internal generation table name")
        columns = expected_columns()[relation_name]
        relation = _qualified(
            self.config.alias,
            relation_name.schema,
            generation_table,
        )
        definitions = ", ".join(
            f"{_quote_identifier(name)} {_column_type(column)}"
            + ("" if column.nullable else " NOT NULL")
            for name, column in columns.items()
        )
        with self.remote_transaction():
            self.trusted_remote_execute(
                f"CREATE TABLE IF NOT EXISTS {relation} ({definitions})"
            )
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
            self.trusted_remote_execute(
                f"COMMENT ON TABLE {relation} IS "
                f"{_quote_literal(TABLE_COMMENTS[relation_name])}"
            )
            for column_name, comment in COLUMN_COMMENTS[relation_name].items():
                self.trusted_remote_execute(
                    f"COMMENT ON COLUMN {relation}."
                    f"{_quote_identifier(column_name)} IS "
                    f"{_quote_literal(comment)}"
                )

    def activate_materialization_generations(
        self,
        generations: Mapping[RelationName, str],
        *,
        activation_id: str,
        transaction: bool = True,
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
        pending: dict[RelationName, str] = {}
        for relation_name, generation_table in generations.items():
            retired_table = (
                f"_atlas_retired_{relation_name.table}_{suffix}"
            )
            rows = self.trusted_remote_rows(
                "SELECT table_name "
                "FROM duckdb_tables() "
                f"WHERE database_name = {_quote_literal(self.config.alias)} "
                f"AND schema_name = {_quote_literal(relation_name.schema)} "
                "AND table_name IN "
                f"({_quote_literal(relation_name.table)}, "
                f"{_quote_literal(generation_table)}, "
                f"{_quote_literal(retired_table)})"
            )
            existing = {str(row[0]) for row in rows}
            if generation_table in existing:
                pending[relation_name] = generation_table
                continue
            if (
                relation_name.table in existing
                and retired_table in existing
            ):
                continue
            raise RuntimeError(
                "materialization generation is missing and the public "
                f"table was not activated by {activation_id}: "
                f"{relation_name.schema}.{generation_table}"
            )
        if not pending:
            return
        if len(pending) != len(generations):
            raise RuntimeError(
                "materialization activation is partially applied"
            )
        with (
            self.remote_transaction()
            if transaction
            else _nullcontext()
        ):
            for relation_name, generation_table in pending.items():
                retired_table = (
                    f"_atlas_retired_{relation_name.table}_{suffix}"
                )
                current = _qualified(
                    self.config.alias,
                    relation_name.schema,
                    relation_name.table,
                )
                generation = _qualified(
                    self.config.alias,
                    relation_name.schema,
                    generation_table,
                )
                self.trusted_remote_execute(
                    f"ALTER TABLE {current} RENAME TO "
                    f"{_quote_identifier(retired_table)}"
                )
                self.trusted_remote_execute(
                    f"ALTER TABLE {generation} RENAME TO "
                    f"{_quote_identifier(relation_name.table)}"
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
        with self.remote_transaction():
            for relation_name in selected:
                if relation_name.schema != MATERIAL_SCHEMA:
                    raise ValueError(
                        "only material tables can be finalized"
                    )
                retired_table = (
                    f"_atlas_retired_{relation_name.table}_{suffix}"
                )
                self.trusted_remote_execute(
                    "DROP TABLE IF EXISTS "
                    f"{_qualified(
                        self.config.alias,
                        relation_name.schema,
                        retired_table,
                    )}"
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
        with self.remote_transaction():
            for table in tables:
                self.trusted_remote_execute(
                    "DROP TABLE IF EXISTS "
                    f"{_qualified(self.config.alias, MATERIAL_SCHEMA, table)}"
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
        cursor = self._connection.execute(sql)
        return cursor.fetchall()

    def trusted_remote_result(
        self,
        sql: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...], list[tuple]]:
        """Execute trusted remote SQL and retain its result-column metadata."""

        cursor = self._connection.execute(sql)
        columns = tuple(str(item[0]) for item in cursor.description)
        types = tuple(str(item[1]) for item in cursor.description)
        return columns, types, cursor.fetchall()

    def trusted_remote_execute(self, sql: str) -> list[tuple]:
        """Execute trusted Atlas SQL directly against DuckLake."""

        return self._connection.execute(sql).fetchall()

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
        json_columns: frozenset[str] = frozenset(),
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
                    f"{_quote_identifier(name)}::JSON "
                    f"AS {_quote_identifier(name)}"
                    if name in json_columns
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

    def close(self) -> None:
        self._connection.close()

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


@contextmanager
def _nullcontext():
    yield
