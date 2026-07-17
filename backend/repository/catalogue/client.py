"""DuckLake bootstrap and connection lifecycle for the repository."""

from __future__ import annotations

import json
from types import TracebackType
from typing import TYPE_CHECKING
from tempfile import TemporaryDirectory
from urllib.parse import urljoin

from ducklake_client import ColumnDef, DuckLake, DuckLakeError, PostgresCatalog, SQLType

from repository.catalogue.config import CatalogueConfig
from repository.catalogue.exceptions import CatalogueSchemaError
from repository.catalogue.schema import (
    ARTIFACT_COLUMNS,
    CATALOGUE_SCHEMA_VERSION,
    CRAWL_COLUMNS,
    CRAWL_STEP_COLUMNS,
    CRAWL_STEPS_TABLE,
    DOCUMENT_COLUMNS,
    INTERNAL_SCHEMA,
    MATERIALIZATION_COVERAGE_COLUMNS,
    MATERIALIZATION_COVERAGE_TABLE,
    expected_columns,
    expected_internal_columns,
)
from dom.schema import ELEMENT_COLUMNS

if TYPE_CHECKING:
    import duckdb


class Catalogue:
    """Thin Atlas boundary over the published ``ducklake-client`` package."""

    def __init__(
        self,
        config: CatalogueConfig,
        *,
        temporary_directory: TemporaryDirectory[str] | None = None,
    ) -> None:
        self.config = config
        self._temporary_directory = temporary_directory
        self._scalar_functions_registered = False
        self.lake = DuckLake(
            catalog=config.catalog,
            storage=config.storage,
            alias=config.alias,
            duckdb=config.duckdb,
            attach=config.attach,
        )

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        connection = self.lake.connection
        if not self._scalar_functions_registered:
            connection.create_function(
                "resolve_url",
                _resolve_url,
                ["VARCHAR", "VARCHAR"],
                "VARCHAR",
            )
            self._scalar_functions_registered = True
        return connection

    @property
    def metadata_schema(self) -> str:
        """Schema containing the attached DuckLake metadata tables."""

        return "public" if isinstance(self.config.catalog, PostgresCatalog) else "main"

    def bootstrap(self) -> None:
        """Create or migrate the catalogue and reject incompatible tables."""

        with self.lake.transaction():
            self.lake.schema.create(self.config.schema)
            self.lake.schema.create("views")
            self.lake.schema.create("macros")
            self.lake.schema.create(INTERNAL_SCHEMA)
            self.lake.schema.create("_atlas_materializations")
            self.lake.table.create(
                "artifacts",
                schema_name=self.config.schema,
                **ARTIFACT_COLUMNS,
            )
            self.lake.table.create(
                "documents",
                schema_name=self.config.schema,
                **DOCUMENT_COLUMNS,
            )
            self.lake.table.create(
                "crawls",
                schema_name=self.config.schema,
                **CRAWL_COLUMNS,
            )
            self.lake.table.create(
                "elements",
                schema_name=self.config.schema,
                **ELEMENT_COLUMNS,
            )
            self.lake.table.create(
                CRAWL_STEPS_TABLE,
                schema_name=INTERNAL_SCHEMA,
                **CRAWL_STEP_COLUMNS,
            )
            self.lake.table.create(
                MATERIALIZATION_COVERAGE_TABLE,
                schema_name=INTERNAL_SCHEMA,
                **MATERIALIZATION_COVERAGE_COLUMNS,
            )
        self._migrate_schema()
        self._configure_layout()
        self._migrate_layout()
        self._configure_inlining()
        from repository.catalogue.macros import install_catalogue_macros

        install_catalogue_macros(self)
        self.validate_schema()

    def _configure_layout(self) -> None:
        """Apply the one physical partition contract for new crawl data."""

        crawls = ".".join(
            _quote_identifier(value)
            for value in (self.config.alias, self.config.schema, "crawls")
        )
        self.connection.execute(
            f"ALTER TABLE {crawls} SET PARTITIONED BY ("
            "year(captured_at), month(captured_at), day(captured_at))"
        )
        elements = ".".join(
            _quote_identifier(value)
            for value in (self.config.alias, self.config.schema, "elements")
        )
        self.connection.execute(
            f"ALTER TABLE {elements} SET PARTITIONED BY (bucket(16, document_id))"
        )

    def _migrate_layout(self) -> None:
        """Rewrite pre-partition files once so the current layout can prune them."""

        self._rewrite_unpartitioned_bucket_files(
            "elements", column="document_id", columns=ELEMENT_COLUMNS
        )

    def _rewrite_unpartitioned_bucket_files(
        self,
        table_name: str,
        *,
        column: str,
        columns: dict[str, ColumnDef],
        stage_in_memory: bool = False,
    ) -> None:
        metadata = _quote_identifier(f"__ducklake_metadata_{self.config.alias}")
        legacy_files = self.connection.execute(
            f"""
            SELECT count(*)
            FROM {metadata}.ducklake_data_file AS df
            JOIN {metadata}.ducklake_table AS t ON t.table_id = df.table_id
            JOIN {metadata}.ducklake_schema AS s ON s.schema_id = t.schema_id
            WHERE s.schema_name = ? AND t.table_name = ?
              AND s.end_snapshot IS NULL AND t.end_snapshot IS NULL
              AND df.end_snapshot IS NULL AND df.partition_id IS NULL
            """,
            [self.config.schema, table_name],
        ).fetchone()
        if legacy_files is None or int(legacy_files[0]) == 0:
            return

        replacement_name = f"__atlas_bucket16_{table_name}"
        table = ".".join(
            _quote_identifier(value)
            for value in (self.config.alias, self.config.schema, table_name)
        )
        replacement = ".".join(
            _quote_identifier(value)
            for value in (self.config.alias, self.config.schema, replacement_name)
        )
        definitions = ", ".join(
            f"{_quote_identifier(name)} {_type_sql(definition.data_type)}"
            + ("" if definition.nullable else " NOT NULL")
            for name, definition in columns.items()
        )
        source = table
        registration = f"__atlas_repartition_source_{table_name}"
        if stage_in_memory:
            # Fan-out tables are small state tables but may contain hundreds of
            # update fragments. Detach the replacement write from those files;
            # DuckLake 1.5 can otherwise fault while dropping the source table
            # in the same transaction that scanned its update history.
            rows = self.connection.execute(f"SELECT * FROM {table}").to_arrow_table()
            self.connection.register(registration, rows)
            source = _quote_identifier(registration)
        try:
            with self.lake.transaction():
                self.connection.execute(f"DROP TABLE IF EXISTS {replacement}")
                self.connection.execute(f"CREATE TABLE {replacement} ({definitions})")
                self.connection.execute(
                    f"ALTER TABLE {replacement} SET PARTITIONED BY "
                    f"(bucket(16, {_quote_identifier(column)}))"
                )
                self.connection.execute(
                    f"INSERT INTO {replacement} BY NAME SELECT * FROM {source}"
                )
                self.connection.execute(f"DROP TABLE {table}")
                self.connection.execute(
                    f"ALTER TABLE {replacement} RENAME TO {_quote_identifier(table_name)}"
                )
                self.set_commit_message(
                    author="Atlas setup",
                    message=f"Repartitioned {self.config.schema}.{table_name}",
                    extra={"partition": f"bucket(16, {column})"},
                )
        finally:
            if stage_in_memory:
                self.connection.unregister(registration)

    def _configure_inlining(self) -> None:
        """Disable metadata inlining for every Atlas-owned physical table."""

        for schema_name in (
            self.config.schema,
            INTERNAL_SCHEMA,
            "_atlas_materializations",
        ):
            for table in self.lake.table.list(schema_name=schema_name):
                self.connection.execute(
                    f"CALL {_quote_identifier(self.config.alias)}.set_option("
                    "'data_inlining_row_limit', 0, schema => ?, table_name => ?)",
                    [schema_name, table.table_name],
                )

    def _migrate_schema(self) -> None:
        """Apply small, idempotent DuckLake schema upgrades owned by Atlas."""

        for obsolete in ("run_crawl_usages", "run_manifests"):
            table = ".".join(
                _quote_identifier(value)
                for value in (self.config.alias, self.config.schema, obsolete)
            )
            with self.lake.transaction():
                self.connection.execute(f"DROP TABLE IF EXISTS {table}")

        info = self.lake.table.info(
            "crawls",
            schema_name=self.config.schema,
            include_summary=False,
            include_row_count=False,
            include_snapshots=False,
        )
        document_id = next(
            (column for column in info.columns if column.name == "document_id"),
            None,
        )
        if document_id is not None and not document_id.nullable:
            table = ".".join(
                _quote_identifier(value)
                for value in (self.config.alias, self.config.schema, "crawls")
            )
            with self.lake.transaction():
                self.connection.execute(
                    f"ALTER TABLE {table} ALTER COLUMN document_id DROP NOT NULL"
                )


    def validate_schema(self) -> None:
        """Validate columns without scanning catalogue data."""

        self._validate_schema_tables(self.config.schema, expected_columns())
        self._validate_schema_tables(INTERNAL_SCHEMA, expected_internal_columns())
        self._validate_layout()
        self._validate_macros()

    def _validate_schema_tables(
        self,
        schema_name: str,
        expected_tables: dict[str, dict[str, ColumnDef]],
    ) -> None:
        for table_name, expected in expected_tables.items():
            try:
                info = self.lake.table.info(
                    table_name,
                    schema_name=schema_name,
                    include_summary=False,
                    include_row_count=False,
                    include_snapshots=False,
                )
            except DuckLakeError as exc:
                raise CatalogueSchemaError(
                    f"catalogue table is missing or unreadable: "
                    f"{schema_name}.{table_name}"
                ) from exc
            actual_columns = [
                (column.name, _normalize_type(column.data_type), column.nullable)
                for column in info.columns
            ]
            normalized_expected = [
                (name, _normalize_type(_type_sql(column.data_type)), column.nullable)
                for name, column in expected.items()
            ]
            if actual_columns != normalized_expected:
                raise CatalogueSchemaError(
                    f"catalogue table {schema_name}.{table_name} does not match "
                    f"schema {CATALOGUE_SCHEMA_VERSION}: "
                    f"expected {normalized_expected!r}, got {actual_columns!r}"
                )

    def _validate_layout(self) -> None:
        metadata = _quote_identifier(f"__ducklake_metadata_{self.config.alias}")
        def partitioning(table_name: str):
            return self.connection.execute(
                f"""
            SELECT pc.partition_key_index, c.column_name, pc.transform
            FROM {metadata}.ducklake_partition_info AS pi
            JOIN {metadata}.ducklake_partition_column AS pc
              ON pc.partition_id = pi.partition_id
             AND pc.table_id = pi.table_id
            JOIN {metadata}.ducklake_table AS t ON t.table_id = pi.table_id
            JOIN {metadata}.ducklake_schema AS s ON s.schema_id = t.schema_id
            JOIN {metadata}.ducklake_column AS c
              ON c.table_id = t.table_id
             AND c.column_id = pc.column_id
             AND c.end_snapshot IS NULL
            WHERE s.schema_name = ?
              AND t.table_name = ?
              AND pi.end_snapshot IS NULL
              AND t.end_snapshot IS NULL
              AND s.end_snapshot IS NULL
            ORDER BY pc.partition_key_index
            """,
                [self.config.schema, table_name],
            ).fetchall()

        crawls = partitioning("crawls")
        expected_crawls = [
            (0, "captured_at", "year"),
            (1, "captured_at", "month"),
            (2, "captured_at", "day"),
        ]
        if crawls != expected_crawls:
            raise CatalogueSchemaError(
                f"catalogue table 'crawls' must be partitioned by "
                f"year/month/day(captured_at), got {crawls!r}"
            )
        elements = partitioning("elements")
        expected_elements = [(0, "document_id", "bucket(16)")]
        if elements != expected_elements:
            raise CatalogueSchemaError(
                "catalogue table 'elements' must be partitioned by "
                f"bucket(16, document_id), got {elements!r}"
            )

    def _validate_macros(self) -> None:
        rows = self.connection.execute(
            """
            SELECT function_name
            FROM duckdb_functions()
            WHERE database_name = ?
              AND schema_name = ?
              AND function_type = 'macro'
              AND function_name IN (
                  'get_attribute', 'has_attribute', 'has_text', 'text_content',
                  'inner_html', 'readable_text'
              )
            ORDER BY function_name
            """,
            [self.config.alias, self.config.schema],
        ).fetchall()
        actual = [str(row[0]) for row in rows]
        expected = [
            "get_attribute",
            "has_attribute",
            "has_text",
            "inner_html",
            "readable_text",
            "text_content",
        ]
        if actual != expected:
            raise CatalogueSchemaError(
                f"catalogue DOM macros do not match the managed contract: "
                f"expected {expected!r}, got {actual!r}"
            )

    def latest_snapshot(self) -> int | None:
        return self.lake.snapshots.latest()

    def last_committed_snapshot(self) -> int | None:
        """Return the snapshot committed most recently by this connection."""

        alias = _quote_identifier(self.config.alias)
        row = self.connection.execute(
            f"SELECT id FROM {alias}.last_committed_snapshot()"
        ).fetchone()
        return None if row is None or row[0] is None else int(row[0])

    def set_commit_message(
        self,
        *,
        author: str,
        message: str,
        extra: dict[str, object] | None = None,
    ) -> None:
        """Annotate the transaction's DuckLake snapshot for operations and audit."""

        alias = _quote_identifier(self.config.alias)
        self.connection.execute(
            f"CALL {alias}.set_commit_message(?, ?, extra_info => ?)",
            [author, message, json.dumps(extra or {}, separators=(",", ":"))],
        )

    def close(self) -> None:
        try:
            self.lake.close()
        finally:
            if self._temporary_directory is not None:
                self._temporary_directory.cleanup()
                self._temporary_directory = None

    def __enter__(self) -> Catalogue:
        _ = self.connection
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

def _normalize_type(value: str) -> str:
    normalized = " ".join(value.upper().split())
    normalized = normalized.replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ")
    normalized = normalized.replace("MAP(VARCHAR, VARCHAR)", "MAP(VARCHAR,VARCHAR)")
    return normalized


def _type_sql(value: str | SQLType) -> str:
    return value.sql() if isinstance(value, SQLType) else value


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _resolve_url(source: str, href: str) -> str:
    return urljoin(source, href)
