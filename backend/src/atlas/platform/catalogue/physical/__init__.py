"""Typed physical DuckLake contracts owned by Atlas."""

from atlas.platform.catalogue.physical.base import (
    CATALOGUE_SCHEMA_VERSION,
    INGEST_SCHEMA,
    MATERIAL_SCHEMA,
    PARTITION_BUCKETS,
    RelationName,
    TableLayout,
)
from atlas.platform.catalogue.physical.ingest import (
    ATTEMPTS,
    CRAWLS,
    DOCUMENTS,
    STEPS,
    VISITS,
)
from atlas.platform.catalogue.physical.material import (
    CONTENT_STATS,
    HTML_ELEMENTS,
    JSONLD_VALUES,
    LINKS,
    LINK_OCCURRENCES,
    PAGES,
    PAGE_HEADS,
    PAGE_OBSERVATIONS,
)

__all__ = [
    "ATTEMPTS",
    "CATALOGUE_SCHEMA_VERSION",
    "CRAWLS",
    "DOCUMENTS",
    "CONTENT_STATS",
    "HTML_ELEMENTS",
    "INGEST_SCHEMA",
    "JSONLD_VALUES",
    "LINKS",
    "LINK_OCCURRENCES",
    "MATERIAL_SCHEMA",
    "PAGES",
    "PAGE_HEADS",
    "PAGE_OBSERVATIONS",
    "PARTITION_BUCKETS",
    "RelationName",
    "STEPS",
    "TableLayout",
    "VISITS",
]
