"""Purpose-specific inputs to the catalogue compiler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .metadata import BoundParameter, CatalogueMetadataSnapshot


@dataclass(frozen=True, slots=True)
class ScalarMacroDefinition:
    schema_name: str
    macro_name: str
    parameters: tuple[str, ...]
    sql: str


@dataclass(frozen=True, slots=True)
class TableMacroDefinition:
    schema_name: str
    macro_name: str
    parameters: tuple[str, ...]
    parameter_defaults: tuple[tuple[str, str], ...]
    sql: str


@dataclass(frozen=True, slots=True)
class ViewDefinition:
    schema_name: str
    view_name: str
    sql: str


@dataclass(frozen=True, slots=True)
class ScalarFunctionDefinition:
    schema_name: str
    function_name: str
    has_side_effects: bool
    stability: str | None


@dataclass(frozen=True, slots=True)
class InteractiveQueryPurpose:
    """Resolve an interactive query using Atlas catalogue definitions."""

    scalar_macros: tuple[ScalarMacroDefinition, ...] = ()
    table_macros: tuple[TableMacroDefinition, ...] = ()
    views: tuple[ViewDefinition, ...] = ()
    scalar_functions: tuple[ScalarFunctionDefinition, ...] = ()
    metadata: CatalogueMetadataSnapshot | None = None
    bound_parameters: tuple[BoundParameter, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogueDefinitionPurpose:
    """Analyze a view or macro body before installing it in DuckLake."""

    kind: Literal["view", "scalar_macro", "table_macro"]
    schema_name: str
    object_name: str
    parameters: tuple[str, ...] = ()
    scalar_macros: tuple[ScalarMacroDefinition, ...] = ()
    table_macros: tuple[TableMacroDefinition, ...] = ()
    views: tuple[ViewDefinition, ...] = ()
    scalar_functions: tuple[ScalarFunctionDefinition, ...] = ()
    metadata: CatalogueMetadataSnapshot | None = None


@dataclass(frozen=True, slots=True)
class GraphEdgePurpose:
    """Compile one bounded crawl-graph navigation query."""

    maximum_limit: int = 100_000
    scalar_macros: tuple[ScalarMacroDefinition, ...] = ()
    table_macros: tuple[TableMacroDefinition, ...] = ()
    views: tuple[ViewDefinition, ...] = ()
    scalar_functions: tuple[ScalarFunctionDefinition, ...] = ()


@dataclass(frozen=True, slots=True)
class FullMaterializationPurpose:
    """Resolve a complete materialization evaluation without key scoping."""

    scalar_macros: tuple[ScalarMacroDefinition, ...] = ()
    table_macros: tuple[TableMacroDefinition, ...] = ()
    views: tuple[ViewDefinition, ...] = ()
    scalar_functions: tuple[ScalarFunctionDefinition, ...] = ()
    metadata: CatalogueMetadataSnapshot | None = None


@dataclass(frozen=True, slots=True)
class KeyedMaterializationPurpose:
    """Optimize one materialization evaluation around its changed keys."""

    source_table: str
    key_columns: tuple[str, ...]
    changed_keys_relation: str = "_atlas_materialization_changed_keys"
    key_rows: tuple[tuple[object, ...], ...] | None = None
    scalar_macros: tuple[ScalarMacroDefinition, ...] = ()
    table_macros: tuple[TableMacroDefinition, ...] = ()
    views: tuple[ViewDefinition, ...] = ()
    scalar_functions: tuple[ScalarFunctionDefinition, ...] = ()
    metadata: CatalogueMetadataSnapshot | None = None
