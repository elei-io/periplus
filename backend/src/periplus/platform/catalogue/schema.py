"""Combined typed contract for Periplus-owned physical DuckLake relations."""

from __future__ import annotations

from periplus.platform.catalogue.physical import (
    ATTEMPTS,
    CATALOGUE_SCHEMA_VERSION,
    CRAWLS,
    DOCUMENTS,
    INGEST_SCHEMA,
    MATERIAL_SCHEMA,
    PARTITION_BUCKETS,
    RelationName,
    STEPS,
    TableLayout,
    VISITS,
)
from periplus.platform.catalogue.physical import ingest
from periplus.platform.catalogue.schema_types import ColumnDef
from periplus.materialization.registry import PROJECTIONS


TABLE_COLUMNS = ingest.TABLE_COLUMNS | {
    spec.relation: spec.physical_columns for spec in PROJECTIONS
}
TABLE_LAYOUTS = ingest.TABLE_LAYOUTS | {
    spec.relation: spec.layout for spec in PROJECTIONS
}
TABLE_COMMENTS = ingest.TABLE_COMMENTS | {
    spec.relation: spec.description for spec in PROJECTIONS
}
COLUMN_COMMENTS = ingest.COLUMN_COMMENTS | {
    spec.relation: spec.column_comments for spec in PROJECTIONS
}


def expected_columns() -> dict[RelationName, dict[str, ColumnDef]]:
    return TABLE_COLUMNS


__all__ = [
    "ATTEMPTS",
    "CATALOGUE_SCHEMA_VERSION",
    "COLUMN_COMMENTS",
    "CRAWLS",
    "DOCUMENTS",
    "INGEST_SCHEMA",
    "MATERIAL_SCHEMA",
    "PARTITION_BUCKETS",
    "RelationName",
    "STEPS",
    "TABLE_COLUMNS",
    "TABLE_COMMENTS",
    "TABLE_LAYOUTS",
    "TableLayout",
    "VISITS",
    "expected_columns",
]
