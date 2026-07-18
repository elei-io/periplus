"""Explicit DuckLake catalogue-view administration."""

from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID

from repository.catalogue.client import Catalogue
from repository.catalogue.query import classify_select, compile_catalogue_definition

VIEW_SCHEMA = "views"
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


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
    column_types: tuple[str, ...] = ()

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.view_name}"


class CatalogueViewStore:
    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

    def list(self) -> list[DuckLakeView]:
        metadata = _quote_identifier(f"__ducklake_metadata_{self.catalogue.config.alias}")
        metadata_schema = _quote_identifier(self.catalogue.metadata_schema)
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
        # DuckLake stores the view SQL, and unqualified names inside it resolve using
        # the caller's current schema. Bind from Atlas main on every fresh connection.
        self._use_main()
        views: list[DuckLakeView] = []
        for row in rows:
            schema_name = str(row[1])
            view_name = str(row[2])
            qualified = ".".join(
                _quote_identifier(value)
                for value in (self.catalogue.config.alias, schema_name, view_name)
            )
            cursor = self.catalogue.connection.execute(
                f"SELECT * FROM {qualified} LIMIT 0"
            )
            views.append(
                DuckLakeView(
                    view_uuid=UUID(str(row[0])),
                    schema_name=schema_name,
                    view_name=view_name,
                    sql=str(row[3]).replace(
                        "{DUCKLAKE_CATALOG}", self.catalogue.config.alias
                    ),
                    columns=tuple(str(column[0]) for column in cursor.description),
                    column_types=tuple(str(column[1]) for column in cursor.description),
                )
            )
        return views

    def get(self, view_uuid: UUID) -> DuckLakeView | None:
        return next((view for view in self.list() if view.view_uuid == view_uuid), None)

    def create(self, *, name: str, sql: str) -> DuckLakeView:
        _validate_name(name)
        compiled = compile_catalogue_definition(sql)
        if any(view.view_name == name for view in self.list()):
            raise CatalogueViewConflictError(f"View {VIEW_SCHEMA}.{name} already exists.")
        self._use_main()
        self.catalogue.connection.execute(
            f"CREATE VIEW {_qualified(self.catalogue, name)} AS {compiled}"
        )
        return self._require_name(name)

    def replace(self, *, current_uuid: UUID, sql: str) -> DuckLakeView:
        compiled = compile_catalogue_definition(sql)
        current = self.get(current_uuid)
        if current is None:
            raise CatalogueViewConflictError(
                "The DuckLake view changed or was removed; refresh before editing."
            )
        self._use_main()
        self.catalogue.connection.execute(
            f"CREATE OR REPLACE VIEW {_qualified(self.catalogue, current.view_name)} "
            f"AS {compiled}"
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
        raise CatalogueViewError(
            "View slug must use lower-case letters, numbers, hyphens, and underscores."
        )


def _qualified(catalogue: Catalogue, name: str) -> str:
    return ".".join(_quote_identifier(part) for part in (catalogue.config.alias, VIEW_SCHEMA, name))


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
