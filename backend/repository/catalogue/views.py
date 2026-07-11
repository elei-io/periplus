"""Explicit DuckLake catalogue-view administration."""

from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID

from ducklake_client import PostgresCatalog

from repository.catalogue.client import Catalogue
from repository.catalogue.query import classify_select

VIEW_SCHEMA = "views"
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class CatalogueViewError(ValueError):
    pass


class CatalogueViewConflictError(CatalogueViewError):
    pass


class CatalogueViewNotFoundError(CatalogueViewError):
    pass


@dataclass(frozen=True, slots=True)
class DuckLakeView:
    view_uuid: UUID
    schema_name: str
    view_name: str
    sql: str
    columns: tuple[str, ...]

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.view_name}"


class CatalogueViewStore:
    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

    def list(self) -> list[DuckLakeView]:
        metadata = _quote_identifier(f"__ducklake_metadata_{self.catalogue.config.alias}")
        metadata_schema = _quote_identifier(
            "public"
            if isinstance(self.catalogue.config.catalog, PostgresCatalog)
            else "main"
        )
        rows = self.catalogue.connection.execute(
            f"""
            SELECT v.view_uuid, s.schema_name, v.view_name, v.sql
            FROM {metadata}.{metadata_schema}.ducklake_view AS v
            JOIN {metadata}.{metadata_schema}.ducklake_schema AS s USING (schema_id)
            WHERE v.end_snapshot IS NULL AND s.end_snapshot IS NULL
              AND s.schema_name = ?
            ORDER BY v.view_name
            """,
            [VIEW_SCHEMA],
        ).fetchall()
        column_rows = self.catalogue.connection.execute(
            """
            SELECT table_name, column_name
            FROM duckdb_columns()
            WHERE database_name = ? AND schema_name = ?
            ORDER BY table_name, column_index
            """,
            [self.catalogue.config.alias, VIEW_SCHEMA],
        ).fetchall()
        columns: dict[str, list[str]] = {}
        for view_name, column_name in column_rows:
            columns.setdefault(str(view_name), []).append(str(column_name))
        return [
            DuckLakeView(
                view_uuid=UUID(str(row[0])),
                schema_name=str(row[1]),
                view_name=str(row[2]),
                sql=str(row[3]).replace(
                    "{DUCKLAKE_CATALOG}", self.catalogue.config.alias
                ),
                columns=tuple(columns.get(str(row[2]), [])),
            )
            for row in rows
        ]

    def get(self, view_uuid: UUID) -> DuckLakeView | None:
        return next((view for view in self.list() if view.view_uuid == view_uuid), None)

    def create(self, *, name: str, sql: str) -> DuckLakeView:
        _validate_name(name)
        classify_select(sql)
        if any(view.view_name == name for view in self.list()):
            raise CatalogueViewConflictError(f"View {VIEW_SCHEMA}.{name} already exists.")
        self._use_main()
        self.catalogue.connection.execute(
            f"CREATE VIEW {_qualified(self.catalogue, name)} AS {sql}"
        )
        return self._require_name(name)

    def replace(self, *, current_uuid: UUID, sql: str) -> DuckLakeView:
        classify_select(sql)
        current = self.get(current_uuid)
        if current is None:
            raise CatalogueViewConflictError(
                "The DuckLake view changed or was removed; refresh before editing."
            )
        self._use_main()
        self.catalogue.connection.execute(
            f"CREATE OR REPLACE VIEW {_qualified(self.catalogue, current.view_name)} AS {sql}"
        )
        return self._require_name(current.view_name)

    def _use_main(self) -> None:
        namespace = ".".join(
            _quote_identifier(part)
            for part in (self.catalogue.config.alias, self.catalogue.config.schema)
        )
        self.catalogue.connection.execute(f"USE {namespace}")

    def drop(self, *, current_uuid: UUID) -> DuckLakeView:
        current = self.get(current_uuid)
        if current is None:
            raise CatalogueViewConflictError(
                "The DuckLake view changed or was removed; refresh before dropping."
            )
        self.catalogue.connection.execute(f"DROP VIEW {_qualified(self.catalogue, current.view_name)}")
        return current

    def _require_name(self, name: str) -> DuckLakeView:
        view = next((view for view in self.list() if view.view_name == name), None)
        if view is None:
            raise CatalogueViewNotFoundError(f"View {VIEW_SCHEMA}.{name} was not found after mutation.")
        return view


def _validate_name(value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise CatalogueViewError("View name must use lower-case letters, numbers, and underscores.")


def _qualified(catalogue: Catalogue, name: str) -> str:
    return ".".join(_quote_identifier(part) for part in (catalogue.config.alias, VIEW_SCHEMA, name))


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
