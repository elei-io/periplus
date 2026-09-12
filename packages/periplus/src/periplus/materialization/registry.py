"""Auto-discovered, one-file Periplus materialization registry."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import pyarrow as pa

from periplus.materialization.document_projection import VisitBatchContext
from periplus.platform.catalogue.physical.base import (
    MATERIAL_SCHEMA,
    RelationName,
    TableLayout,
)
from periplus.platform.catalogue.schema_types import ColumnDef, MapType

OwnershipGrain = Literal["content", "visit"]
PartitionKind = Literal["bucket", "day", "month", "year"]
Projector = Callable[[VisitBatchContext], pa.Table]


@dataclass(frozen=True, slots=True)
class ProjectionColumn:
    """One physical, Arrow, and documentation column declaration."""

    name: str
    arrow_type: pa.DataType
    duckdb_type: str | MapType
    comment: str
    nullable: bool = True

    @property
    def arrow_field(self) -> pa.Field:
        return pa.field(
            self.name,
            self.arrow_type,
            nullable=self.nullable,
        )

    @property
    def physical_column(self) -> ColumnDef:
        return ColumnDef(self.duckdb_type, nullable=self.nullable)


@dataclass(frozen=True, slots=True)
class PartitionTransform:
    """One projection-owned physical partition decision."""

    kind: PartitionKind
    column: str
    buckets: int | None = None

    def __post_init__(self) -> None:
        if self.kind == "bucket":
            if self.buckets is None or self.buckets < 1:
                raise ValueError("bucket partitions require a positive bucket count")
        elif self.buckets is not None:
            raise ValueError(f"{self.kind} partitions do not accept buckets")

    @property
    def layout_sql(self) -> str:
        if self.kind == "bucket":
            return f"bucket({self.buckets}, {self.column})"
        return f"{self.kind}({self.column})"

    @property
    def grouping_sql(self) -> str:
        if self.kind == "bucket":
            return (
                f"(murmur3_32({self.column}) & 2147483647) "
                f"% {self.buckets}"
            )
        return (
            f"date_diff('{self.kind}', DATE '1970-01-01', "
            f"CAST({self.column} AS DATE))"
        )


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    """The complete contract owned by one projection module."""

    name: str
    ownership_grain: OwnershipGrain
    columns: tuple[ProjectionColumn, ...]
    partitioning: tuple[PartitionTransform, ...]
    sort_order: tuple[str, ...]
    projector: Projector
    description: str
    identity_columns: tuple[str, ...]
    validation_queries: tuple[str, ...] = ()
    content_presence_predicate: str | None = None
    implementation_dependencies: tuple[str, ...] = ()

    @property
    def relation(self) -> RelationName:
        return RelationName(MATERIAL_SCHEMA, self.name)

    @property
    def arrow_schema(self) -> pa.Schema:
        return pa.schema([column.arrow_field for column in self.columns])

    @property
    def physical_columns(self) -> dict[str, ColumnDef]:
        return {
            column.name: column.physical_column
            for column in self.columns
        }

    @property
    def column_comments(self) -> dict[str, str]:
        return {column.name: column.comment for column in self.columns}

    @property
    def layout(self) -> TableLayout:
        return TableLayout(
            partition_by=tuple(
                transform.layout_sql for transform in self.partitioning
            ),
            sort_by=self.sort_order,
        )

    def rows(self, context: VisitBatchContext) -> pa.Table:
        output = self.projector(context)
        if output.schema != self.arrow_schema:
            raise ValueError(
                f"{self.name} projector returned {output.schema}, "
                f"expected {self.arrow_schema}"
            )
        return output


def discover_projections(
    package_name: str = "periplus.materialization.projections",
) -> tuple[ProjectionSpec, ...]:
    """Load one ``PROJECTION`` declaration from every package module."""

    package = importlib.import_module(package_name)
    discovered: list[ProjectionSpec] = []
    for module_info in sorted(
        pkgutil.iter_modules(package.__path__),
        key=lambda item: item.name,
    ):
        if module_info.name.startswith("_") or module_info.ispkg:
            continue
        module = importlib.import_module(
            f"{package_name}.{module_info.name}"
        )
        projection = getattr(module, "PROJECTION", None)
        if not isinstance(projection, ProjectionSpec):
            raise TypeError(
                f"{module.__name__} must export one ProjectionSpec as PROJECTION"
            )
        if projection.name != module_info.name:
            raise ValueError(
                f"{module.__name__} declares {projection.name!r}; "
                "the projection name must equal its filename"
            )
        _validate_projection(projection)
        discovered.append(projection)
    names = [projection.name for projection in discovered]
    if len(names) != len(set(names)):
        raise ValueError("materialization projection names must be unique")
    content_projections = [
        projection
        for projection in discovered
        if projection.ownership_grain == "content"
    ]
    presence_projections = [
        projection
        for projection in content_projections
        if projection.content_presence_predicate is not None
    ]
    if content_projections and len(presence_projections) != 1:
        raise ValueError(
            "content-grain projections require exactly one presence marker"
        )
    return tuple(discovered)


def _validate_projection(spec: ProjectionSpec) -> None:
    if spec.ownership_grain not in ('content', 'visit'):
        raise ValueError(f'invalid ownership grain for {spec.name}')
    if not spec.name or not spec.name.isidentifier() or spec.name.startswith("_"):
        raise ValueError(f"invalid projection name {spec.name!r}")
    if not spec.columns:
        raise ValueError(f"{spec.name} must declare at least one column")
    names = tuple(column.name for column in spec.columns)
    if len(names) != len(set(names)):
        raise ValueError(f"{spec.name} has duplicate columns")
    for transform in spec.partitioning:
        if transform.column not in names:
            raise ValueError(
                f"{spec.name} partitions by unknown column {transform.column}"
            )
    kinds = tuple(transform.kind for transform in spec.partitioning)
    if len(kinds) != len(set(kinds)):
        raise ValueError(
            f"{spec.name} cannot repeat a partition transform kind"
        )
    if not spec.identity_columns or not set(spec.identity_columns).issubset(names):
        raise ValueError(
            f"{spec.name} must declare identity columns from its schema"
        )
    if (
        spec.content_presence_predicate is not None
        and spec.ownership_grain != "content"
    ):
        raise ValueError(
            f"{spec.name} cannot provide content presence at visit grain"
        )
    if (
        spec.content_presence_predicate is not None
        and "content_sha256" not in names
    ):
        raise ValueError(
            f"{spec.name} content presence requires content_sha256"
        )


PROJECTIONS = discover_projections()
BY_NAME = {spec.name: spec for spec in PROJECTIONS}
RELATIONS = {spec.name: spec.relation for spec in PROJECTIONS}
PROJECTION_NAMES = tuple(spec.name for spec in PROJECTIONS)
CONTENT_PRESENCE_PROJECTION = next(
    (
        spec
        for spec in PROJECTIONS
        if spec.content_presence_predicate is not None
    ),
    None,
)


def registry_digest(
    projections: tuple[ProjectionSpec, ...] = PROJECTIONS,
) -> str:
    """Return the schema/lifecycle identity frozen into every generation."""

    payload = [
        {
            "name": spec.name,
            "relation": spec.relation.qualified,
            "ownership_grain": spec.ownership_grain,
            "columns": [
                {
                    "name": column.name,
                    "arrow_type": str(column.arrow_type),
                    "duckdb_type": str(column.duckdb_type),
                    "nullable": column.nullable,
                    "comment": column.comment,
                }
                for column in spec.columns
            ],
            "partitioning": [
                {
                    "kind": transform.kind,
                    "column": transform.column,
                    "buckets": transform.buckets,
                }
                for transform in spec.partitioning
            ],
            "sort_order": spec.sort_order,
            "description": spec.description,
            "identity_columns": spec.identity_columns,
            "validation_queries": spec.validation_queries,
            "content_presence_predicate": spec.content_presence_predicate,
            "implementation_dependencies": {
                name: hashlib.sha256(inspect.getsource(importlib.import_module(name)).encode()).hexdigest()
                for name in spec.implementation_dependencies
            },
            "implementation_sha256": hashlib.sha256(
                inspect.getsource(
                    importlib.import_module(spec.projector.__module__)
                ).encode()
            ).hexdigest(),
        }
        for spec in projections
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


REGISTRY_DIGEST = registry_digest()


def validate_registry() -> None:
    """Re-run all one-file projection invariants."""

    for spec in PROJECTIONS:
        _validate_projection(spec)


validate_registry()
