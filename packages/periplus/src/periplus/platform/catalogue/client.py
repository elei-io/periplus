"""Direct DuckLake connection and logical schema boundary."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager

import pyarrow as pa

from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.connection import DuckLakeConnectionFactory
from periplus.platform.catalogue.exceptions import CatalogueSchemaError
from periplus.platform.catalogue.schema import (
    COLUMN_COMMENTS,
    INGEST_SCHEMA,
    MATERIAL_SCHEMA,
    TABLE_COMMENTS,
    TABLE_LAYOUTS,
    RelationName,
    expected_columns,
)
from periplus.platform.catalogue.public import (
    install_public_catalogue,
    validate_public_catalogue,
)
from periplus.platform.catalogue.storage import DuckLakeStorageProtocol

_INTERNAL_TABLE_NAME = re.compile(r"^_periplus_[a-z0-9_]+$")


class Catalogue:
    """One process-local connection to Periplus's shared DuckLake."""

    def __init__(
        self,
        config: CatalogueConfig,
        *,
        duckdb_config: Mapping[str, str] | None = None,
        load_cdc: bool = False,
        read_only: bool = False,
        override_data_path: bool = False,
        protocol: DuckLakeStorageProtocol | None = None,
    ) -> None:
        self.config = config
        factory = DuckLakeConnectionFactory(
            config,
            duckdb_config,
            protocol=protocol,
        )
        self.storage = factory.storage
        self._connection = factory.connect(
            load_cdc=load_cdc,
            read_only=read_only,
            override_data_path=override_data_path,
        )
        self._use_schema_if_available()

    @property
    def trusted_connection(self):
        """Return the raw local connection for trusted Periplus infrastructure SQL."""

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
        active_registry_matches = self._active_registry_matches()
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
            for relation_name, layout in TABLE_LAYOUTS.items():
                self._set_parquet_layout(relation_name, relation_name.table, layout)
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
        """Inspect the native dataset schema without reading operational state.

        A query-only process has no control Postgres credentials. Workers use
        Postgres's generation digest separately to gate live materialization.
        """
        from periplus.materialization.registry import PROJECTIONS
        rows = self.trusted_remote_rows(
            "SELECT table_name, column_name FROM duckdb_columns() "
            f"WHERE database_name = {_quote_literal(self.config.alias)} AND schema_name = 'material'")
        actual: dict[str, set[str]] = {}
        for table, column in rows:
            actual.setdefault(str(table), set()).add(str(column))
        # An empty lake can be initialized directly. An existing generation must
        # contain every projection before setup may replace its public views.
        return not actual or all(
            actual.get(spec.name) == set(spec.physical_columns)
            for spec in PROJECTIONS
        )

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

        self._set_parquet_layout(relation_name, generation_table, layout)

    def _set_parquet_layout(self, relation_name, table_name, layout) -> None:
        # DuckLake requires the table's creation transaction to be committed first.
        # Planning cannot publish batches until both creation and settings succeed.
        if layout.parquet_row_group_size is not None:
            self.trusted_remote_execute(
                f"CALL {_quote_identifier(self.config.alias)}.set_option("
                f"'parquet_row_group_size', {layout.parquet_row_group_size}, "
                f"table_name => {_quote_literal(table_name)}, "
                f"schema => {_quote_literal(relation_name.schema)})"
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
                f"_periplus_retired_{relation_name.table}_{suffix}"
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
                    f"_periplus_retired_{relation_name.table}_{suffix}"
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
                # A newly added projection has no old active table. Create its
                # empty retirement marker inside the atomic swap, preserving the
                # same replay proof as replacements without exposing empty data.
                self.trusted_remote_execute(
                    f"CREATE TABLE IF NOT EXISTS {current} AS "
                    f"SELECT * FROM {generation} LIMIT 0"
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
                    f"_periplus_retired_{relation_name.table}_{suffix}"
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
        """Execute trusted Periplus SQL directly against DuckLake."""

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
        registration = f"_periplus_upload_{id(table):x}"
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
            # A newly provisioned lake has no Periplus schema until bootstrap.
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
