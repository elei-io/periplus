"""Combined typed contract for Atlas-owned physical DuckLake relations."""

from __future__ import annotations

from atlas.platform.catalogue.physical import (
    ATTEMPTS,
    CATALOGUE_SCHEMA_VERSION,
    CRAWLS,
    DOCUMENTS,
    HTML_DOCUMENTS,
    HTML_ELEMENTS,
    INGEST_SCHEMA,
    JSONLD_VALUES,
    LINKS,
    LINK_OBSERVATIONS,
    MATERIAL_SCHEMA,
    PAGES,
    PAGE_OBSERVATIONS,
    PARTITION_BUCKETS,
    RelationName,
    STEPS,
    TableLayout,
    VISITS,
)
from atlas.platform.catalogue.physical import ingest, material
from atlas.platform.catalogue.schema_types import ColumnDef


TABLE_COLUMNS = ingest.TABLE_COLUMNS | material.TABLE_COLUMNS
TABLE_LAYOUTS = ingest.TABLE_LAYOUTS | material.TABLE_LAYOUTS
TABLE_COMMENTS = ingest.TABLE_COMMENTS | material.TABLE_COMMENTS
COLUMN_COMMENTS = ingest.COLUMN_COMMENTS | material.COLUMN_COMMENTS


def expected_columns() -> dict[RelationName, dict[str, ColumnDef]]:
    return TABLE_COLUMNS


__all__ = [
    "ATTEMPTS",
    "CATALOGUE_SCHEMA_VERSION",
    "COLUMN_COMMENTS",
    "CRAWLS",
    "DOCUMENTS",
    "HTML_DOCUMENTS",
    "HTML_ELEMENTS",
    "INGEST_SCHEMA",
    "JSONLD_VALUES",
    "LINKS",
    "LINK_OBSERVATIONS",
    "MATERIAL_SCHEMA",
    "PAGES",
    "PAGE_OBSERVATIONS",
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
