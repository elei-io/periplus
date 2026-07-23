"""Persistent DuckLake scalar-macro administration."""

from __future__ import annotations

from dataclasses import dataclass
import re

from repository.catalogue.client import Catalogue

SCALAR_MACRO_SCHEMA = "macros"
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class CatalogueScalarMacroError(ValueError):
    pass


class CatalogueScalarMacroConflictError(CatalogueScalarMacroError):
    pass


@dataclass(frozen=True, slots=True)
class DuckLakeScalarMacro:
    schema_name: str
    macro_name: str
    parameters: tuple[str, ...]

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.macro_name}"


class CatalogueScalarMacroStore:
    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

    def list(self) -> list[DuckLakeScalarMacro]:
        rows = self.catalogue.remote_rows(
            """
            SELECT schema_name, function_name, parameters
            FROM duckdb_functions()
            WHERE database_name = """
            + _quote_literal(self.catalogue.config.alias)
            + """
              AND schema_name = """
            + _quote_literal(SCALAR_MACRO_SCHEMA)
            + """
              AND function_type = 'macro'
            ORDER BY function_name
            """
        )
        return [
            DuckLakeScalarMacro(
                schema_name=str(row[0]),
                macro_name=str(row[1]),
                parameters=tuple(str(value) for value in (row[2] or [])),
            )
            for row in rows
        ]

    def get(self, name: str) -> DuckLakeScalarMacro | None:
        return next((macro for macro in self.list() if macro.macro_name == name), None)

    def create(
        self, *, name: str, parameters: list[str], sql: str
    ) -> DuckLakeScalarMacro:
        normalized = _validate(name, parameters, sql)
        if self.get(name) is not None:
            raise CatalogueScalarMacroConflictError(
                f"Scalar macro {SCALAR_MACRO_SCHEMA}.{name} already exists."
            )
        self._execute_definition(
            "CREATE MACRO", name=name, parameters=normalized, sql=sql
        )
        return self._require(name)

    def replace(
        self, *, name: str, parameters: list[str], sql: str
    ) -> DuckLakeScalarMacro:
        normalized = _validate(name, parameters, sql)
        self._execute_definition(
            "CREATE OR REPLACE MACRO", name=name, parameters=normalized, sql=sql
        )
        return self._require(name)

    def drop(self, *, name: str) -> None:
        _validate_name(name, "Scalar macro name")
        self.catalogue.remote_execute(
            f"DROP MACRO IF EXISTS {_qualified(self.catalogue, name)}"
        )

    def _execute_definition(
        self,
        operation: str,
        *,
        name: str,
        parameters: tuple[str, ...],
        sql: str,
    ) -> None:
        signature = ", ".join(_quote_identifier(value) for value in parameters)
        self.catalogue.remote_execute(
            f"{operation} {_qualified(self.catalogue, name)}({signature}) "
            f"AS ({sql.strip()})"
        )

    def _require(self, name: str) -> DuckLakeScalarMacro:
        macro = self.get(name)
        if macro is None:
            raise CatalogueScalarMacroError(
                f"Scalar macro {SCALAR_MACRO_SCHEMA}.{name} was not found after mutation."
            )
        return macro


def _validate(name: str, parameters: list[str], sql: str) -> tuple[str, ...]:
    _validate_name(name, "Scalar macro name")
    if not sql.strip():
        raise CatalogueScalarMacroError("Scalar macro SQL must not be empty.")
    normalized = tuple(value.strip() for value in parameters)
    if len(normalized) > 32:
        raise CatalogueScalarMacroError("Scalar macros may have at most 32 parameters.")
    for parameter in normalized:
        _validate_name(parameter, "Parameter name")
    if len(set(normalized)) != len(normalized):
        raise CatalogueScalarMacroError("Scalar macro parameter names must be unique.")
    return normalized


def _validate_name(value: str, label: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise CatalogueScalarMacroError(
            f"{label} must use lower-case letters, numbers, and underscores."
        )


def _qualified(catalogue: Catalogue, name: str) -> str:
    return ".".join(
        _quote_identifier(part)
        for part in (catalogue.config.alias, SCALAR_MACRO_SCHEMA, name)
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
