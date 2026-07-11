"""The versioned Atlas DOM column contract."""

from ducklake_client import ColumnDef, MapType

DOM_SCHEMA_VERSION = 1

# Durable DuckLake shape. The page-local encoder emits every column except document_id;
# repository ingestion injects the content identity when committing a projection.
ELEMENT_COLUMNS: dict[str, ColumnDef] = {
    "document_id": ColumnDef("VARCHAR", nullable=False),
    "element_index": ColumnDef("INTEGER", nullable=False),
    "parent_index": ColumnDef("INTEGER"),
    "subtree_end_index": ColumnDef("INTEGER", nullable=False),
    "depth": ColumnDef("INTEGER", nullable=False),
    "tag": ColumnDef("VARCHAR", nullable=False),
    "namespace_uri": ColumnDef("VARCHAR"),
    "attributes": ColumnDef(MapType("VARCHAR", "VARCHAR"), nullable=False),
    "text_direct": ColumnDef("VARCHAR", nullable=False),
    "text_tail": ColumnDef("VARCHAR", nullable=False),
}
