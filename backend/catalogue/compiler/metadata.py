"""Immutable catalogue metadata consumed by compiler purposes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .purpose import (
    InteractiveQueryPurpose,
    ScalarFunctionDefinition,
    ScalarMacroDefinition,
    TableMacroDefinition,
    ViewDefinition,
)

PartitionTransform = Literal[
    "identity",
    "bucket",
    "year",
    "month",
    "day",
    "hour",
]


@dataclass(frozen=True, slots=True)
class BoundParameter:
    """One value known during planning while SQL keeps its placeholder."""

    name: str
    value: object


@dataclass(frozen=True, slots=True)
class ColumnStatistics:
    column_name: str
    distinct_count: int | None = None
    null_count: int | None = None
    minimum: object | None = None
    maximum: object | None = None


@dataclass(frozen=True, slots=True)
class PartitionColumn:
    column_name: str
    transform: PartitionTransform = "identity"
    bucket_count: int | None = None

    def __post_init__(self) -> None:
        if self.transform == "bucket":
            if self.bucket_count is None or self.bucket_count <= 0:
                raise ValueError("bucket partitions require a positive bucket_count")
        elif self.bucket_count is not None:
            raise ValueError("bucket_count is only valid for bucket partitions")


@dataclass(frozen=True, slots=True)
class SortKey:
    expression: str
    direction: Literal["ASC", "DESC"] = "ASC"
    null_order: Literal["NULLS FIRST", "NULLS LAST"] | None = None


@dataclass(frozen=True, slots=True)
class TableRelationship:
    """A compiler-trusted logical relation not enforceable by DuckLake."""

    columns: tuple[str, ...]
    target_schema: str
    target_table: str
    target_columns: tuple[str, ...]
    optional: bool = False

    def __post_init__(self) -> None:
        if not self.columns or len(self.columns) != len(self.target_columns):
            raise ValueError("relationship columns must be non-empty and have equal arity")


@dataclass(frozen=True, slots=True)
class ManagedTableMetadata:
    """Stable identity and physical facts for one managed DuckLake table."""

    schema_name: str
    table_name: str
    table_uuid: str
    contract_version: str | None = None
    estimated_rows: int | None = None
    file_count: int | None = None
    file_size_bytes: int | None = None
    stable_key: tuple[str, ...] = ()
    sort_keys: tuple[SortKey, ...] = ()
    partition_columns: tuple[PartitionColumn, ...] = ()
    relationships: tuple[TableRelationship, ...] = ()
    column_statistics: tuple[ColumnStatistics, ...] = ()

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    def statistics_for(self, column_name: str) -> ColumnStatistics | None:
        normalized = column_name.lower()
        return next(
            (
                statistics
                for statistics in self.column_statistics
                if statistics.column_name.lower() == normalized
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class CatalogueMetadataSnapshot:
    """One definition and physical-metadata revision used for a compilation."""

    revision: str
    scalar_macros: tuple[ScalarMacroDefinition, ...] = ()
    table_macros: tuple[TableMacroDefinition, ...] = ()
    views: tuple[ViewDefinition, ...] = ()
    scalar_functions: tuple[ScalarFunctionDefinition, ...] = ()
    tables: tuple[ManagedTableMetadata, ...] = ()

    def interactive_purpose(
        self,
        *,
        bound_parameters: tuple[BoundParameter, ...] = (),
    ) -> InteractiveQueryPurpose:
        return InteractiveQueryPurpose(
            metadata=self,
            bound_parameters=bound_parameters,
        )

    def table(
        self,
        table_name: str,
        *,
        schema_name: str | None = None,
    ) -> ManagedTableMetadata | None:
        normalized_table = table_name.lower()
        normalized_schema = schema_name.lower() if schema_name else None
        matches = tuple(
            table
            for table in self.tables
            if table.table_name.lower() == normalized_table
            and (
                normalized_schema is None
                or table.schema_name.lower() == normalized_schema
            )
        )
        return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True, slots=True)
class ScanEstimate:
    relation: str
    table_uuid: str
    estimated_rows_read: int | None
    estimated_bytes_read: int | None
    partition_predicates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompilationEstimate:
    authored_scans: tuple[ScanEstimate, ...]
    executable_scans: tuple[ScanEstimate, ...]
    estimated_rows_avoided: int | None
    estimated_bytes_avoided: int | None
