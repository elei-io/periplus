"""Persistent user-owned DuckLake table-macro administration."""

from __future__ import annotations

from dataclasses import dataclass
import re

from repository.catalogue.client import Catalogue
from repository.catalogue.query import CatalogueQueryError, compile_catalogue_definition

TABLE_MACRO_SCHEMA = "macros"
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class CatalogueTableMacroError(ValueError):
    pass


class CatalogueTableMacroConflictError(CatalogueTableMacroError):
    pass


@dataclass(frozen=True, slots=True)
class DuckLakeTableMacro:
    schema_name: str
    macro_name: str
    parameters: tuple[str, ...]

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.macro_name}"


class CatalogueTableMacroStore:
    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

    def list(self) -> list[DuckLakeTableMacro]:
        rows = self.catalogue.remote_rows(
            """
            SELECT schema_name, function_name, parameters
            FROM duckdb_functions()
            WHERE database_name = """
            + _quote_literal(self.catalogue.config.alias)
            + """
              AND schema_name = """
            + _quote_literal(TABLE_MACRO_SCHEMA)
            + """
              AND function_type = 'table_macro'
            ORDER BY function_name
            """
        )
        return [
            DuckLakeTableMacro(
                schema_name=str(row[0]),
                macro_name=str(row[1]),
                parameters=tuple(str(value) for value in (row[2] or [])),
            )
            for row in rows
        ]

    def get(self, name: str) -> DuckLakeTableMacro | None:
        return next((macro for macro in self.list() if macro.macro_name == name), None)

    def create(
        self,
        *,
        name: str,
        parameters: list[str],
        sql: str,
        parameter_defaults: dict[str, str] | None = None,
    ) -> DuckLakeTableMacro:
        normalized, defaults = _validate(name, parameters, parameter_defaults)
        if self.get(name) is not None:
            raise CatalogueTableMacroConflictError(
                f"Table macro {TABLE_MACRO_SCHEMA}.{name} already exists."
            )
        self._execute_definition(
            "CREATE MACRO",
            name=name,
            parameters=normalized,
            parameter_defaults=defaults,
            sql=sql,
        )
        return self._require(name)

    def replace(
        self,
        *,
        name: str,
        parameters: list[str],
        sql: str,
        parameter_defaults: dict[str, str] | None = None,
    ) -> DuckLakeTableMacro:
        normalized, defaults = _validate(name, parameters, parameter_defaults)
        self._execute_definition(
            "CREATE OR REPLACE MACRO",
            name=name,
            parameters=normalized,
            parameter_defaults=defaults,
            sql=sql,
        )
        return self._require(name)

    def drop(self, *, name: str) -> None:
        _validate_name(name, "Table macro name")
        self.catalogue.remote_execute(
            f"DROP MACRO TABLE IF EXISTS {_qualified(self.catalogue, name)}"
        )

    def _execute_definition(
        self,
        operation: str,
        *,
        name: str,
        parameters: tuple[str, ...],
        parameter_defaults: dict[str, str],
        sql: str,
    ) -> None:
        compiled = compile_catalogue_definition(sql)
        signature = ", ".join(
            (
                f"{_quote_identifier(value)} := {parameter_defaults[value]}"
                if value in parameter_defaults
                else _quote_identifier(value)
            )
            for value in parameters
        )
        self.catalogue.remote_execute(
            f"{operation} {_qualified(self.catalogue, name)}({signature}) "
            f"AS TABLE ({compiled})"
        )

    def _require(self, name: str) -> DuckLakeTableMacro:
        macro = self.get(name)
        if macro is None:
            raise CatalogueTableMacroError(
                f"Table macro {TABLE_MACRO_SCHEMA}.{name} was not found after mutation."
            )
        return macro


def _validate(
    name: str,
    parameters: list[str],
    parameter_defaults: dict[str, str] | None = None,
) -> tuple[tuple[str, ...], dict[str, str]]:
    _validate_name(name, "Table macro name")
    normalized = tuple(value.strip() for value in parameters)
    defaults = {
        key.strip(): value.strip()
        for key, value in (parameter_defaults or {}).items()
    }
    if len(normalized) > 32:
        raise CatalogueTableMacroError("Table macros may have at most 32 parameters.")
    for parameter in normalized:
        _validate_name(parameter, "Parameter name")
    if len(set(normalized)) != len(normalized):
        raise CatalogueTableMacroError("Table macro parameter names must be unique.")
    unknown_defaults = set(defaults) - set(normalized)
    if unknown_defaults:
        names = ", ".join(sorted(unknown_defaults))
        raise CatalogueTableMacroError(
            f"Defaults reference unknown table macro parameters: {names}."
        )
    for parameter, expression in defaults.items():
        if not expression:
            raise CatalogueTableMacroError(
                f"Default expression for {parameter} must not be empty."
            )
        try:
            compile_catalogue_definition(f"SELECT {expression}")
        except CatalogueQueryError as exc:
            raise CatalogueTableMacroError(
                f"Invalid default expression for {parameter}: {exc}"
            ) from exc
    seen_default = False
    for parameter in normalized:
        if parameter in defaults:
            seen_default = True
        elif seen_default:
            raise CatalogueTableMacroError(
                "Required parameters cannot follow parameters with defaults."
            )
    return normalized, defaults


def _validate_name(value: str, label: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise CatalogueTableMacroError(
            f"{label} must use lower-case letters, numbers, and underscores."
        )


def _qualified(catalogue: Catalogue, name: str) -> str:
    return ".".join(
        _quote_identifier(part)
        for part in (catalogue.config.alias, TABLE_MACRO_SCHEMA, name)
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
