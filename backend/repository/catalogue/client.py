"""DuckLake bootstrap and connection lifecycle for the repository."""

from __future__ import annotations

import json
from types import TracebackType
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from ducklake_client import DuckLake, DuckLakeError, PostgresCatalog, SQLType

from repository.catalogue.config import CatalogueConfig
from repository.catalogue.exceptions import CatalogueSchemaError
from repository.catalogue.schema import (
    CATALOGUE_SCHEMA_VERSION,
    CRAWL_COLUMNS,
    CRAWL_MATERIALIZATION_FANOUT_COLUMNS,
    CRAWL_MATERIALIZATION_FANOUT_MEMBER_COLUMNS,
    DOCUMENT_COLUMNS,
    MATERIALIZATION_SCOPE_RESULT_COLUMNS,
    expected_columns,
)
from dom.schema import ELEMENT_COLUMNS

if TYPE_CHECKING:
    import duckdb


class Catalogue:
    """Thin Atlas boundary over the published ``ducklake-client`` package."""

    def __init__(self, config: CatalogueConfig) -> None:
        self.config = config
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
            self.lake.schema.create("materialized")
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
                "materialization_scope_results",
                schema_name=self.config.schema,
                **MATERIALIZATION_SCOPE_RESULT_COLUMNS,
            )
            self.lake.table.create(
                "crawl_materialization_fanouts",
                schema_name=self.config.schema,
                **CRAWL_MATERIALIZATION_FANOUT_COLUMNS,
            )
            self.lake.table.create(
                "crawl_materialization_fanout_members",
                schema_name=self.config.schema,
                **CRAWL_MATERIALIZATION_FANOUT_MEMBER_COLUMNS,
            )
        self._migrate_schema()
        self._configure_layout()
        self._configure_inlining()
        from repository.catalogue.macros import install_catalogue_macros

        install_catalogue_macros(self)
        self.validate_schema()

    def _configure_layout(self) -> None:
        """Apply the one physical partition contract for new crawl data."""

        table = ".".join(
            _quote_identifier(value)
            for value in (self.config.alias, self.config.schema, "crawls")
        )
        self.connection.execute(
            f"ALTER TABLE {table} SET PARTITIONED BY ("
            "year(captured_at), month(captured_at), day(captured_at))"
        )

    def _configure_inlining(self) -> None:
        """Persist hot-ingestion table thresholds in DuckLake metadata."""

        for table_name, row_limit in (
            ("documents", 1000),
            ("crawls", 1000),
            ("elements", 16000),
        ):
            self.connection.execute(
                f"CALL {_quote_identifier(self.config.alias)}.set_option("
                "'data_inlining_row_limit', ?, schema => ?, table_name => ?)",
                [row_limit, self.config.schema, table_name],
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

        coverage = self.lake.table.info(
            "materialization_scope_results",
            schema_name=self.config.schema,
            include_summary=False,
            include_row_count=False,
            include_snapshots=False,
        )
        coverage_names = {column.name for column in coverage.columns}
        if "materialized_view_id" in coverage_names and "materialization_id" not in coverage_names:
            table = ".".join(
                _quote_identifier(value)
                for value in (
                    self.config.alias,
                    self.config.schema,
                    "materialization_scope_results",
                )
            )
            with self.lake.transaction():
                self.connection.execute(
                    f"ALTER TABLE {table} RENAME COLUMN "
                    "materialized_view_id TO materialization_id"
                )
            coverage = self.lake.table.info(
                "materialization_scope_results",
                schema_name=self.config.schema,
                include_summary=False,
                include_row_count=False,
                include_snapshots=False,
            )
        if not any(column.name == "partition_value" for column in coverage.columns):
            table = ".".join(
                _quote_identifier(value)
                for value in (
                    self.config.alias,
                    self.config.schema,
                    "materialization_scope_results",
                )
            )
            with self.lake.transaction():
                self.connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN partition_value DATE"
                )

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

        for table_name, expected in expected_columns().items():
            try:
                info = self.lake.table.info(
                    table_name,
                    schema_name=self.config.schema,
                    include_summary=False,
                    include_row_count=False,
                    include_snapshots=False,
                )
            except DuckLakeError as exc:
                raise CatalogueSchemaError(
                    f"catalogue table is missing or unreadable: {table_name}"
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
                    f"catalogue table {table_name!r} does not match "
                    f"schema v{CATALOGUE_SCHEMA_VERSION}: "
                    f"expected {normalized_expected!r}, got {actual_columns!r}"
                )
        self._validate_layout()
        self._validate_macros()

    def _validate_layout(self) -> None:
        metadata = _quote_identifier(f"__ducklake_metadata_{self.config.alias}")
        rows = self.connection.execute(
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
              AND t.table_name = 'crawls'
              AND pi.end_snapshot IS NULL
              AND t.end_snapshot IS NULL
              AND s.end_snapshot IS NULL
            ORDER BY pc.partition_key_index
            """,
            [self.config.schema],
        ).fetchall()
        expected = [
            (0, "captured_at", "year"),
            (1, "captured_at", "month"),
            (2, "captured_at", "day"),
        ]
        if rows != expected:
            raise CatalogueSchemaError(
                f"catalogue table 'crawls' must be partitioned by "
                f"year/month/day(captured_at), got {rows!r}"
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
        self.lake.close()

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
