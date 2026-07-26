"""Compile a definition against the authoritative live DuckLake snapshot."""

from __future__ import annotations

from atlas_sql import AtlasCompiler, CompilationResult

from .client import Catalogue
from .compiler_definitions import read_catalogue_compiler_definitions


def compile_definition_authoring(
    catalogue: Catalogue,
    sql: str,
    *,
    kind: str,
    schema_name: str,
    object_name: str,
    parameters: tuple[str, ...] = (),
) -> CompilationResult:
    with catalogue.remote_transaction():
        definitions = read_catalogue_compiler_definitions(
            catalogue.trusted_connection,
            catalogue_alias=catalogue.config.alias,
        )
    return AtlasCompiler.embedded(
        catalogue_revision=definitions.revision,
    ).compile(
        sql,
        purpose=definitions.definition_purpose(
            kind=kind,
            schema_name=schema_name,
            object_name=object_name,
            parameters=parameters,
        ),
        coverage_source="definition_authoring",
    )
