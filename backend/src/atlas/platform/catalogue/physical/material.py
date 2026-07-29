"""Typed physical contract for rebuildable ``material.*`` relations."""

from __future__ import annotations

from atlas.platform.catalogue.physical.base import (
    MATERIAL_SCHEMA,
    PARTITION_BUCKETS,
    RelationName,
    TableLayout,
)
from atlas.platform.catalogue.schema_types import ColumnDef, MapType


CONTENT_STATS = RelationName(MATERIAL_SCHEMA, "content_stats")
HTML_ELEMENTS = RelationName(MATERIAL_SCHEMA, "html_elements")
JSONLD_VALUES = RelationName(MATERIAL_SCHEMA, "jsonld_values")
PAGES = RelationName(MATERIAL_SCHEMA, "pages")
PAGE_OBSERVATIONS = RelationName(MATERIAL_SCHEMA, "page_observations")
PAGE_HEADS = RelationName(MATERIAL_SCHEMA, "page_heads")
LINKS = RelationName(MATERIAL_SCHEMA, "links")
LINK_OCCURRENCES = RelationName(MATERIAL_SCHEMA, "link_occurrences")


TABLE_COLUMNS: dict[RelationName, dict[str, ColumnDef]] = {
    CONTENT_STATS: {
        "content_sha256": ColumnDef("VARCHAR", nullable=False),
        "content_bytes": ColumnDef("BIGINT", nullable=False),
        "dom_element_count": ColumnDef("INTEGER"),
        "dom_max_depth": ColumnDef("INTEGER"),
    },
    HTML_ELEMENTS: {
        "content_sha256": ColumnDef("VARCHAR", nullable=False),
        "element_index": ColumnDef("INTEGER", nullable=False),
        "parent_index": ColumnDef("INTEGER"),
        "subtree_end_index": ColumnDef("INTEGER", nullable=False),
        "depth": ColumnDef("INTEGER", nullable=False),
        "child_index": ColumnDef("INTEGER", nullable=False),
        "tag": ColumnDef("VARCHAR", nullable=False),
        "namespace": ColumnDef("VARCHAR", nullable=False),
        "attributes": ColumnDef(MapType("VARCHAR", "VARCHAR"), nullable=False),
        "text_direct": ColumnDef("VARCHAR", nullable=False),
        "text_tail": ColumnDef("VARCHAR", nullable=False),
    },
    JSONLD_VALUES: {
        "content_sha256": ColumnDef("VARCHAR", nullable=False),
        "element_index": ColumnDef("INTEGER", nullable=False),
        "type_terms": ColumnDef("VARCHAR[]", nullable=False),
        "value": ColumnDef("VARIANT", nullable=False),
    },
    PAGES: {
        "page_id": ColumnDef("UUID", nullable=False),
        "normalized_url": ColumnDef("VARCHAR", nullable=False),
        "scheme": ColumnDef("VARCHAR", nullable=False),
        "hostname": ColumnDef("VARCHAR", nullable=False),
        "port": ColumnDef("INTEGER"),
        "path": ColumnDef("VARCHAR", nullable=False),
        "query": ColumnDef("VARCHAR"),
        "registrable_domain": ColumnDef("VARCHAR"),
    },
    PAGE_OBSERVATIONS: {
        "page_id": ColumnDef("UUID", nullable=False),
        "visit_id": ColumnDef("UUID", nullable=False),
        "document_id": ColumnDef("UUID"),
        "visit_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    },
    PAGE_HEADS: {
        "page_id": ColumnDef("UUID", nullable=False),
        "visit_id": ColumnDef("UUID", nullable=False),
        "visit_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    },
    LINKS: {
        "link_id": ColumnDef("UUID", nullable=False),
        "source_page_id": ColumnDef("UUID", nullable=False),
        "target_page_id": ColumnDef("UUID", nullable=False),
        "source_url": ColumnDef("VARCHAR", nullable=False),
        "target_url": ColumnDef("VARCHAR", nullable=False),
        "relation_scope": ColumnDef("VARCHAR", nullable=False),
        "first_seen_at": ColumnDef("TIMESTAMPTZ", nullable=False),
        "last_seen_at": ColumnDef("TIMESTAMPTZ", nullable=False),
        "visit_count": ColumnDef("BIGINT", nullable=False),
        "distinct_content_count": ColumnDef("BIGINT", nullable=False),
        "occurrence_count": ColumnDef("BIGINT", nullable=False),
    },
    LINK_OCCURRENCES: {
        "occurrence_id": ColumnDef("UUID", nullable=False),
        "link_id": ColumnDef("UUID", nullable=False),
        "visit_id": ColumnDef("UUID", nullable=False),
        "document_id": ColumnDef("UUID", nullable=False),
        "content_sha256": ColumnDef("VARCHAR", nullable=False),
        "element_index": ColumnDef("INTEGER", nullable=False),
        "raw_href": ColumnDef("VARCHAR", nullable=False),
        "observed_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    },
}


TABLE_LAYOUTS = {
    CONTENT_STATS: TableLayout(
        partition_by=(f"bucket({PARTITION_BUCKETS}, content_sha256)",),
        sort_by=("content_sha256 ASC",),
    ),
    HTML_ELEMENTS: TableLayout(
        partition_by=(f"bucket({PARTITION_BUCKETS}, content_sha256)",),
        sort_by=("content_sha256 ASC", "element_index ASC"),
    ),
    JSONLD_VALUES: TableLayout(
        partition_by=(f"bucket({PARTITION_BUCKETS}, content_sha256)",),
        sort_by=("content_sha256 ASC", "element_index ASC"),
    ),
    PAGES: TableLayout(
        partition_by=(f"bucket({PARTITION_BUCKETS}, page_id)",),
        sort_by=("page_id ASC", "normalized_url ASC"),
    ),
    PAGE_OBSERVATIONS: TableLayout(
        partition_by=(f"bucket({PARTITION_BUCKETS}, page_id)",),
        sort_by=("page_id ASC", "visit_at DESC", "visit_id ASC"),
    ),
    PAGE_HEADS: TableLayout(
        partition_by=(f"bucket({PARTITION_BUCKETS}, page_id)",),
        sort_by=("page_id ASC",),
    ),
    LINKS: TableLayout(
        partition_by=(f"bucket({PARTITION_BUCKETS}, source_page_id)",),
        sort_by=("source_page_id ASC", "target_page_id ASC", "link_id ASC"),
    ),
    LINK_OCCURRENCES: TableLayout(
        partition_by=(f"bucket({PARTITION_BUCKETS}, link_id)",),
        sort_by=("link_id ASC", "document_id ASC", "element_index ASC"),
    ),
}


TABLE_COMMENTS = {
    CONTENT_STATS: "Typed compiler statistics for immutable content.",
    HTML_ELEMENTS: "Rebuildable structural projections of immutable HTML content.",
    JSONLD_VALUES: "Rebuildable parsed JSON-LD payloads embedded in HTML content.",
    PAGES: "Rebuildable identities for normalized URLs observed through visits.",
    PAGE_OBSERVATIONS: (
        "Rebuildable page-to-visit evidence index for terminal visits."
    ),
    PAGE_HEADS: "Latest terminal visit pointer for each normalized page.",
    LINKS: (
        "Rebuildable normalized source-target pairs with historical rollups."
    ),
    LINK_OCCURRENCES: (
        "Rebuildable document-owned evidence for observed HTML link occurrences."
    ),
}


COLUMN_COMMENTS = {
    CONTENT_STATS: {
        "content_sha256": "Identity of immutable logical content bytes.",
        "content_bytes": "Size of the uncompressed logical content.",
        "dom_element_count": (
            "Number of projected DOM elements, null without a completed DOM."
        ),
        "dom_max_depth": (
            "Maximum projected DOM depth, null without a completed DOM."
        ),
    },
    HTML_ELEMENTS: {
        "content_sha256": "Identity of projected immutable HTML bytes.",
        "element_index": "Zero-based depth-first document position.",
        "parent_index": "Parent element index, null for the root.",
        "subtree_end_index": "Exclusive end of this element subtree.",
        "depth": "Element depth from the root.",
        "child_index": "Zero-based position among element siblings.",
        "tag": "Normalized local tag name.",
        "namespace": "Normalized element namespace.",
        "attributes": "Attribute names and string values.",
        "text_direct": "Text directly inside this element before child elements.",
        "text_tail": "Text following this element within its parent.",
    },
    JSONLD_VALUES: {
        "content_sha256": "Identity of the containing immutable HTML bytes.",
        "element_index": "Source script element in material.html_elements.",
        "type_terms": "Distinct raw @type strings found in the payload.",
        "value": "Complete parsed JSON-LD payload.",
    },
    PAGES: {
        "page_id": "Versioned deterministic identity derived from normalized_url.",
        "normalized_url": "Unique normalized URL represented by this page.",
        "scheme": "Normalized URL scheme.",
        "hostname": "Normalized hostname.",
        "port": "Explicit non-default port, otherwise null.",
        "path": "Normalized URL path.",
        "query": "Preserved query string, null when absent.",
        "registrable_domain": "Public-suffix-aware domain when derivable.",
    },
    PAGE_OBSERVATIONS: {
        "page_id": "Deterministic normalized page identity observed by this visit.",
        "visit_id": "Visit supplying this page observation and its provenance.",
        "document_id": "Document produced by this visit, when one was retained.",
        "visit_at": "Terminal ordering time for this visit.",
    },
    PAGE_HEADS: {
        "page_id": "Normalized page identity whose latest visit is selected.",
        "visit_id": "Deterministically latest observed visit for the page.",
        "visit_at": "Terminal ordering time used to select the latest visit.",
    },
    LINKS: {
        "link_id": (
            "Versioned deterministic identity of the directed normalized page pair."
        ),
        "source_page_id": "Deterministic identity of the normalized source URL.",
        "target_page_id": "Deterministic identity of the normalized target URL.",
        "source_url": "Normalized fragment-free URL where the link was observed.",
        "target_url": "Normalized fragment-free URL resolved from the observed href.",
        "relation_scope": "Most-specific deterministic source-target relationship.",
        "first_seen_at": "Earliest retained occurrence time for this link.",
        "last_seen_at": "Latest retained occurrence time for this link.",
        "visit_count": "Visits in which this link occurred at least once.",
        "distinct_content_count": (
            "Distinct immutable content identities containing this link."
        ),
        "occurrence_count": "Total retained DOM occurrences of this link.",
    },
    LINK_OCCURRENCES: {
        "occurrence_id": "Stable identity of this document element occurrence.",
        "link_id": "Stable link identity supported by this occurrence.",
        "visit_id": "Visit during which this link occurrence was observed.",
        "document_id": "Document observation that owns this link occurrence.",
        "content_sha256": "Immutable HTML content containing the source anchor.",
        "element_index": "Source anchor position in material.html_elements.",
        "raw_href": "Exact non-empty href attribute observed on the source anchor.",
        "observed_at": "Time the containing document representation was captured.",
    },
}
