"""Install and validate the registry-derived public DuckLake catalogue."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from importlib.resources import files
from typing import Literal, Protocol

from atlas.platform.catalogue.exceptions import CatalogueSchemaError

PUBLIC_CATALOGUE_VERSION = "5.0.1"
WEB_SCHEMA = "web"
DOM_SCHEMA = "dom"
PUBLIC_SCHEMAS = (WEB_SCHEMA, DOM_SCHEMA)


class CatalogueConnection(Protocol):
    def remote_transaction(self): ...

    def trusted_remote_execute(self, sql: str) -> list[tuple]: ...

    def trusted_remote_rows(self, sql: str) -> list[tuple]: ...


@dataclass(frozen=True, slots=True)
class CatalogueObject:
    kind: Literal["macro", "view", "table_macro"]
    name: str
    resource: str
    columns: tuple[str, ...] = ()
    arguments_sql: str | None = None
    parameters: tuple[tuple[str, str], ...] = ()
    return_type: str | None = None
    exposed: bool = True
    schema: Literal["web", "dom"] = WEB_SCHEMA
    comment: str | None = None
    column_comments: tuple[tuple[str, str], ...] = ()
    requires_functions: frozenset[str] = frozenset()
    requires_relations: frozenset[str] = frozenset()


def public_objects() -> tuple[CatalogueObject, ...]:
    """Return public objects whose declared physical dependencies exist."""

    from atlas.materialization.registry import PROJECTIONS
    from atlas.platform.catalogue.public_registry import PUBLIC_OBJECTS

    available_relations = {
        spec.relation.qualified for spec in PROJECTIONS
    }
    return tuple(
        item
        for item in PUBLIC_OBJECTS
        if item.requires_relations.issubset(available_relations)
    )


def known_public_objects() -> tuple[CatalogueObject, ...]:
    """Return all declared objects, including extension-gated objects."""

    return public_objects()


def _available_required_functions(
    catalogue: CatalogueConnection,
) -> frozenset[str]:
    required = frozenset(
        function
        for item in known_public_objects()
        for function in item.requires_functions
    )
    if not required:
        return frozenset()
    values = ", ".join(_quote_literal(name) for name in sorted(required))
    return frozenset(
        str(name)
        for (name,) in catalogue.trusted_remote_rows(
            "SELECT DISTINCT function_name FROM duckdb_functions() "
            f"WHERE function_name IN ({values})"
        )
    )


def dom_selector_extension_available(
    catalogue: CatalogueConnection,
) -> bool:
    required = frozenset(
        {"atlas_dom_select_first_keyed", "atlas_dom_select_all_keyed"}
    )
    return required.issubset(_available_required_functions(catalogue))


def installed_public_objects(
    catalogue: CatalogueConnection,
) -> tuple[CatalogueObject, ...]:
    available = _available_required_functions(catalogue)
    return tuple(
        item
        for item in known_public_objects()
        if item.requires_functions.issubset(available)
    )


def install_public_catalogue(
    catalogue: CatalogueConnection,
    *,
    transaction: bool = True,
) -> None:
    """Atomically replace the complete registry-derived public contract."""

    declared = known_public_objects()
    installed = installed_public_objects(catalogue)
    for item in declared:
        if item.kind == "view":
            _validated_view_comments(item)
    _validate_unique_objects(declared)

    root = files("atlas.platform.catalogue").joinpath("sql")
    boundary = catalogue.remote_transaction() if transaction else nullcontext()
    with boundary:
        for schema in PUBLIC_SCHEMAS:
            catalogue.trusted_remote_execute(
                f"CREATE SCHEMA IF NOT EXISTS {schema}"
            )
            _drop_superseded_public_objects(
                catalogue,
                schema,
                declared=declared,
            )
        installed_names = {
            (item.schema, item.kind, item.name) for item in installed
        }
        for item in declared:
            if (
                item.kind in {"macro", "table_macro"}
                and (item.schema, item.kind, item.name)
                not in installed_names
            ):
                catalogue.trusted_remote_execute(
                    f"DROP MACRO IF EXISTS {item.schema}."
                    f"{_quote_identifier(item.name)}"
                )
        for item in installed:
            sql = root.joinpath(
                item.schema,
                item.resource,
            ).read_text(encoding="utf-8")
            catalogue.trusted_remote_execute(sql)
            if item.kind == "view":
                _install_view_comments(catalogue, item)


def _validate_unique_objects(
    objects: tuple[CatalogueObject, ...],
) -> None:
    identities = [
        (item.schema, item.kind, item.name) for item in objects
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("public catalogue objects must be uniquely owned")


def _install_view_comments(
    catalogue: CatalogueConnection,
    item: CatalogueObject,
) -> None:
    comment, _column_comments = _validated_view_comments(item)
    qualified = (
        f"{_quote_identifier(item.schema)}.{_quote_identifier(item.name)}"
    )
    catalogue.trusted_remote_execute(
        f"COMMENT ON VIEW {qualified} IS {_quote_literal(comment)}"
    )


def _validated_view_comments(
    item: CatalogueObject,
) -> tuple[str, dict[str, str]]:
    qualified = f"{item.schema}.{item.name}"
    if item.comment is None:
        raise ValueError(f"{qualified} has no view comment")
    column_comments = dict(item.column_comments)
    comment_columns = tuple(column_comments)
    if len(column_comments) != len(item.column_comments):
        raise ValueError(f"{qualified} has duplicate column comments")
    if comment_columns != item.columns:
        raise ValueError(
            f"{qualified} comment columns: expected {item.columns}, "
            f"got {comment_columns}"
        )
    return item.comment, column_comments


def _drop_superseded_public_objects(
    catalogue: CatalogueConnection,
    schema: str,
    *,
    declared: tuple[CatalogueObject, ...],
) -> None:
    expected_macros = {
        (item.name, item.kind)
        for item in declared
        if item.schema == schema
        and item.kind in {"macro", "table_macro"}
    }
    existing_macros = {
        (str(row[0]), str(row[1]))
        for row in catalogue.trusted_remote_rows(
            "SELECT function_name, function_type FROM duckdb_functions() "
            f"WHERE schema_name = {_quote_literal(schema)} "
            "AND function_type IN ('macro', 'table_macro')"
        )
    }
    for name, _kind in sorted(existing_macros - expected_macros):
        catalogue.trusted_remote_execute(
            f"DROP MACRO {schema}.{_quote_identifier(name)}"
        )

    expected_views = {
        item.name
        for item in declared
        if item.schema == schema and item.kind == "view"
    }
    existing_views = {
        str(row[0])
        for row in catalogue.trusted_remote_rows(
            "SELECT view_name FROM duckdb_views() "
            f"WHERE schema_name = {_quote_literal(schema)}"
        )
    }
    for name in sorted(existing_views - expected_views):
        catalogue.trusted_remote_execute(
            f"DROP VIEW {schema}.{_quote_identifier(name)}"
        )


def validate_public_catalogue(catalogue: CatalogueConnection) -> None:
    """Fail when the installed public catalogue differs from discovery."""

    errors: list[str] = []
    declared = known_public_objects()
    installed = installed_public_objects(catalogue)
    _validate_unique_objects(declared)
    for schema in PUBLIC_SCHEMAS:
        expected_views = {
            item.name
            for item in installed
            if item.schema == schema and item.kind == "view"
        }
        actual_views = {
            str(row[0])
            for row in catalogue.trusted_remote_rows(
                "SELECT view_name FROM duckdb_views() "
                f"WHERE schema_name = {_quote_literal(schema)}"
            )
        }
        if actual_views != expected_views:
            errors.append(
                f"{schema} views: expected {sorted(expected_views)}, "
                f"got {sorted(actual_views)}"
            )

        expected_macros = {
            (item.name, item.kind)
            for item in installed
            if item.schema == schema
            and item.kind in {"macro", "table_macro"}
        }
        actual_macros = {
            (str(row[0]), str(row[1]))
            for row in catalogue.trusted_remote_rows(
                "SELECT function_name, function_type "
                "FROM duckdb_functions() "
                f"WHERE schema_name = {_quote_literal(schema)} "
                "AND function_type IN ('macro', 'table_macro')"
            )
        }
        if actual_macros != expected_macros:
            errors.append(
                f"{schema} macros: expected {sorted(expected_macros)}, "
                f"got {sorted(actual_macros)}"
            )

    for item in installed:
        qualified = f"{item.schema}.{item.name}"
        if not item.columns:
            continue
        try:
            if item.kind == "view":
                rows = catalogue.trusted_remote_rows(
                    f"DESCRIBE {qualified}"
                )
            else:
                if item.arguments_sql is None:
                    raise ValueError(
                        f"{qualified} has no validation arguments"
                    )
                rows = catalogue.trusted_remote_rows(
                    f"DESCRIBE SELECT * FROM {qualified}"
                    f"({item.arguments_sql})"
                )
        except Exception as exc:
            errors.append(f"{qualified}: {exc}")
            continue
        actual_columns = tuple(str(row[0]) for row in rows)
        if actual_columns != item.columns:
            errors.append(
                f"{qualified}: expected columns {item.columns}, "
                f"got {actual_columns}"
            )
        if item.kind == "view":
            _validate_view_comments(catalogue, item, errors)

    try:
        rows = catalogue.trusted_remote_rows(
            f"SELECT {WEB_SCHEMA}._catalogue_version()"
        )
        actual_version = str(rows[0][0])
    except Exception as exc:
        errors.append(f"web catalogue version: {exc}")
    else:
        if actual_version != PUBLIC_CATALOGUE_VERSION:
            errors.append(
                "public catalogue version: expected "
                f"{PUBLIC_CATALOGUE_VERSION}, got {actual_version}"
            )
    if errors:
        raise CatalogueSchemaError("; ".join(errors))


def _validate_view_comments(
    catalogue: CatalogueConnection,
    item: CatalogueObject,
    errors: list[str],
) -> None:
    qualified = f"{item.schema}.{item.name}"
    try:
        expected_comment, _column_comments = _validated_view_comments(item)
        rows = catalogue.trusted_remote_rows(
            "SELECT comment FROM duckdb_views() "
            f"WHERE schema_name = {_quote_literal(item.schema)} "
            f"AND view_name = {_quote_literal(item.name)}"
        )
    except Exception as exc:
        errors.append(f"{qualified} comments: {exc}")
        return
    actual_comment = rows[0][0] if len(rows) == 1 else None
    if actual_comment != expected_comment:
        errors.append(f"{qualified}: missing or stale view comment")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
