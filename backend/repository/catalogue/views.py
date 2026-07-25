"""Explicit DuckLake catalogue-view administration."""

from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlglot import exp, parse_one

from repository.catalogue.client import Catalogue
from repository.catalogue.query import classify_select, compile_catalogue_definition

VIEW_SCHEMA = "views"
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_DESCRIPTION_UNSET = object()


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

    def definitions(self) -> list[DuckLakeView]:
        """List view identities and SQL without binding their dependencies."""

        alias = _quote_literal(self.catalogue.config.alias)
        rows = self.catalogue.trusted_remote_rows(
            """
            SELECT schema_name, view_name, sql
            FROM duckdb_views()
            """
            f"WHERE database_name = {alias} "
            f"AND schema_name = {_quote_literal(VIEW_SCHEMA)} "
            "ORDER BY view_name"
        )
        return [
            DuckLakeView(
                view_uuid=_view_uuid(
                    self.catalogue.config.alias,
                    str(row[0]),
                    str(row[1]),
                ),
                schema_name=str(row[0]),
                view_name=str(row[1]),
                sql=_view_query(str(row[2])).replace(
                    "{DUCKLAKE_CATALOG}", self.catalogue.config.alias
                ),
                columns=(),
            )
            for row in rows
        ]

    def definition_for_uuid(self, view_uuid: UUID) -> DuckLakeView | None:
        return next(
            (
                view
                for view in self.definitions()
                if view.view_uuid == view_uuid
            ),
            None,
        )

    def definition_for_name(self, view_name: str) -> DuckLakeView | None:
        return next(
            (
                view
                for view in self.definitions()
                if view.view_name == view_name
            ),
            None,
        )

    def list(self) -> list[DuckLakeView]:
        # DuckLake stores the view SQL, and unqualified names inside it resolve using
        # the caller's current schema. Bind from Atlas main on every fresh connection.
        self._use_main()
        return [self._describe(view) for view in self.definitions()]

    def get(self, view_uuid: UUID) -> DuckLakeView | None:
        view = self.definition_for_uuid(view_uuid)
        return self._describe(view) if view is not None else None

    def create(
        self,
        *,
        name: str,
        sql: str,
        description: str | None | object = _DESCRIPTION_UNSET,
    ) -> DuckLakeView:
        _validate_name(name)
        compiled = compile_catalogue_definition(sql)
        if self.definition_for_name(name) is not None:
            raise CatalogueViewConflictError(f"View {VIEW_SCHEMA}.{name} already exists.")
        self._use_main()
        self.catalogue.trusted_remote_execute(
            f"CREATE VIEW {_qualified(self.catalogue, name)} AS {compiled}"
        )
        if description is not _DESCRIPTION_UNSET:
            self.set_comment(
                name=name,
                description=description if isinstance(description, str) else None,
            )
        return self._require_name(name)

    def replace(
        self,
        *,
        current_uuid: UUID,
        sql: str,
        description: str | None | object = _DESCRIPTION_UNSET,
    ) -> DuckLakeView:
        compiled = compile_catalogue_definition(sql)
        current_name = self.name_for_uuid(current_uuid)
        if current_name is None:
            raise CatalogueViewConflictError(
                "The DuckLake view changed or was removed; refresh before editing."
            )
        self._use_main()
        self.catalogue.trusted_remote_execute(
            f"CREATE OR REPLACE VIEW {_qualified(self.catalogue, current_name)} "
            f"AS {compiled}"
        )
        if description is not _DESCRIPTION_UNSET:
            self.set_comment(
                name=current_name,
                description=description if isinstance(description, str) else None,
            )
        return self._require_name(current_name)

    def set_comment(self, *, name: str, description: str | None) -> None:
        _validate_name(name)
        self.catalogue.trusted_remote_execute(
            f"COMMENT ON VIEW {_qualified(self.catalogue, name)} IS "
            f"{_quote_literal(description) if description is not None else 'NULL'}"
        )

    def name_for_uuid(self, view_uuid: UUID) -> str | None:
        """Look up identity without binding the view's possibly broken SQL."""

        alias = _quote_literal(self.catalogue.config.alias)
        rows = self.catalogue.trusted_remote_rows(
            "SELECT view_name FROM duckdb_views() "
            f"WHERE database_name = {alias} "
            f"AND schema_name = {_quote_literal(VIEW_SCHEMA)}"
        )
        return next(
            (
                str(row[0])
                for row in rows
                if _view_uuid(
                    self.catalogue.config.alias,
                    VIEW_SCHEMA,
                    str(row[0]),
                )
                == view_uuid
            ),
            None,
        )

    def _use_main(self) -> None:
        # Basin sessions start in the selected lake's main schema. Mutation
        # targets are fully qualified.
        return

    def drop(self, *, current_uuid: UUID) -> DuckLakeView:
        current_name = self.name_for_uuid(current_uuid)
        if current_name is None:
            raise CatalogueViewConflictError(
                "The DuckLake view changed or was removed; refresh before dropping."
            )
        try:
            current = self.get(current_uuid)
        except Exception:
            current = None
        if current is None:
            current = DuckLakeView(
                view_uuid=current_uuid,
                schema_name=VIEW_SCHEMA,
                view_name=current_name,
                sql="",
                columns=(),
            )
        self.catalogue.trusted_remote_execute(
            f"DROP VIEW {_qualified(self.catalogue, current.view_name)}"
        )
        return current

    def _require_name(self, name: str) -> DuckLakeView:
        definition = self.definition_for_name(name)
        view = self._describe(definition) if definition is not None else None
        if view is None:
            raise CatalogueViewNotFoundError(f"View {VIEW_SCHEMA}.{name} was not found after mutation.")
        return view

    def _describe(self, view: DuckLakeView) -> DuckLakeView:
        qualified = ".".join(
            _quote_identifier(value)
            for value in (
                self.catalogue.config.alias,
                view.schema_name,
                view.view_name,
            )
        )
        columns = self.catalogue.trusted_remote_rows(f"DESCRIBE {qualified}")
        return DuckLakeView(
            view_uuid=view.view_uuid,
            schema_name=view.schema_name,
            view_name=view.view_name,
            sql=view.sql,
            columns=tuple(str(column[0]) for column in columns),
            column_types=tuple(str(column[1]) for column in columns),
        )


def _validate_name(value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise CatalogueViewError(
            "View slug must use lower-case letters, numbers, hyphens, and underscores."
        )


def _qualified(catalogue: Catalogue, name: str) -> str:
    return ".".join(_quote_identifier(part) for part in (catalogue.config.alias, VIEW_SCHEMA, name))


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _view_uuid(alias: str, schema_name: str, view_name: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"duckbasin:{alias}:view:{schema_name}.{view_name}",
    )


def _view_query(sql: str) -> str:
    statement = parse_one(sql, dialect="duckdb")
    if isinstance(statement, exp.Create) and statement.expression is not None:
        return statement.expression.sql(dialect="duckdb")
    return sql
