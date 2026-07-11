"""Explicit full-refresh materialized-view operations."""

from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID

from ducklake_client import PostgresCatalog

from repository.catalogue.client import Catalogue
from repository.catalogue.query import classify_select

MATERIALIZED_SCHEMA = "materialized"
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class MaterializedViewError(ValueError):
    pass


class MaterializedViewConflictError(MaterializedViewError):
    pass


@dataclass(frozen=True, slots=True)
class MaterializedTable:
    table_uuid: UUID
    name: str
    row_count: int
    columns: tuple[tuple[str, str, bool], ...]


class MaterializedViewStore:
    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

    def create(self, *, name: str, sql: str) -> MaterializedTable:
        _validate_name(name)
        classify_select(sql)
        if any(item.table_name == name for item in self.catalogue.lake.table.list(schema_name=MATERIALIZED_SCHEMA)):
            raise MaterializedViewConflictError(f"Table {MATERIALIZED_SCHEMA}.{name} already exists.")
        self._use_main()
        self.catalogue.connection.execute(f"CREATE TABLE {_qualified(self.catalogue, name)} AS {sql}")
        return self.inspect(name)

    def refresh(self, *, name: str, expected_uuid: UUID, sql: str) -> MaterializedTable:
        current = self.inspect(name)
        if current.table_uuid != expected_uuid:
            raise MaterializedViewConflictError("The materialized table changed; refresh the page before retrying.")
        classify_select(sql)
        self._use_main()
        self.catalogue.connection.execute(f"CREATE OR REPLACE TABLE {_qualified(self.catalogue, name)} AS {sql}")
        return self.inspect(name)

    def drop(self, *, name: str, expected_uuid: UUID) -> None:
        current = self.inspect(name)
        if current.table_uuid != expected_uuid:
            raise MaterializedViewConflictError("The materialized table changed; refresh the page before dropping it.")
        self.catalogue.connection.execute(f"DROP TABLE {_qualified(self.catalogue, name)}")

    def inspect(self, name: str) -> MaterializedTable:
        try:
            info = self.catalogue.lake.table.info(
                name,
                schema_name=MATERIALIZED_SCHEMA,
                include_summary=False,
                include_row_count=True,
                include_snapshots=False,
            )
        except Exception as exc:
            raise MaterializedViewConflictError(f"Materialized table {name} is unavailable.") from exc
        metadata_catalog = f'"__ducklake_metadata_{self.catalogue.config.alias}"'
        metadata_schema = "public" if isinstance(
            self.catalogue.config.catalog, PostgresCatalog
        ) else "main"
        identity = self.catalogue.connection.execute(
            f"""
            SELECT t.table_uuid
            FROM {metadata_catalog}.{metadata_schema}.ducklake_table AS t
            JOIN {metadata_catalog}.{metadata_schema}.ducklake_schema AS s USING (schema_id)
            WHERE t.end_snapshot IS NULL AND s.end_snapshot IS NULL
              AND t.table_name = ? AND s.schema_name = ?
            """,
            [name, MATERIALIZED_SCHEMA],
        ).fetchone()
        if identity is None:
            raise MaterializedViewConflictError(f"Materialized table {name} has no DuckLake identity.")
        return MaterializedTable(
            table_uuid=UUID(str(identity[0])),
            name=name,
            row_count=int(info.row_count or 0),
            columns=tuple((column.name, column.data_type, column.nullable) for column in info.columns),
        )

    def _use_main(self) -> None:
        self.catalogue.connection.execute(
            f'USE "{self.catalogue.config.alias}"."{self.catalogue.config.schema}"'
        )


def _validate_name(value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise MaterializedViewError("Name must use lower-case letters, numbers, and underscores.")


def _qualified(catalogue: Catalogue, name: str) -> str:
    return ".".join(f'"{part}"' for part in (catalogue.config.alias, MATERIALIZED_SCHEMA, name))
