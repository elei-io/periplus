"""DuckLake bootstrap and connection lifecycle for the repository."""

from __future__ import annotations

from types import TracebackType
from typing import TYPE_CHECKING

from ducklake_client import DuckLake, DuckLakeError, SQLType

from repository.catalogue.config import CatalogueConfig
from repository.catalogue.exceptions import CatalogueSchemaError
from repository.catalogue.schema import (
    CATALOGUE_SCHEMA_VERSION,
    CRAWL_COLUMNS,
    DOCUMENT_COLUMNS,
    expected_columns,
)
from dom.schema import ELEMENT_COLUMNS

if TYPE_CHECKING:
    import duckdb


class Catalogue:
    """Thin Atlas boundary over the published ``ducklake-client`` package."""

    def __init__(self, config: CatalogueConfig) -> None:
        self.config = config
        self.lake = DuckLake(
            catalog=config.catalog,
            storage=config.storage,
            alias=config.alias,
            duckdb=config.duckdb,
            attach=config.attach,
        )

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        return self.lake.connection

    def bootstrap(self) -> None:
        """Create or migrate the catalogue and reject incompatible tables."""

        with self.lake.transaction():
            self.lake.schema.create(self.config.schema)
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
        self._migrate_schema()
        self.validate_schema()

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

    def latest_snapshot(self) -> int | None:
        return self.lake.snapshots.latest()

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
