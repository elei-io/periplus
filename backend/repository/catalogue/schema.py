"""Authoritative physical contract for Atlas-owned DuckLake relations."""

from __future__ import annotations

from dataclasses import dataclass

from schema_types import ColumnDef, MapType

CATALOGUE_SCHEMA_VERSION = "2.3.0"
INGEST_SCHEMA = "ingest"
MATERIAL_SCHEMA = "material"


@dataclass(frozen=True, slots=True, order=True)
class RelationName:
    schema: str
    table: str

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"


@dataclass(frozen=True, slots=True)
class TableLayout:
    partition_by: tuple[str, ...] = ()
    sort_by: tuple[str, ...] = ()


CRAWLS = RelationName(INGEST_SCHEMA, "crawls")
VISITS = RelationName(INGEST_SCHEMA, "visits")
ATTEMPTS = RelationName(INGEST_SCHEMA, "attempts")
STEPS = RelationName(INGEST_SCHEMA, "steps")
DOCUMENTS = RelationName(INGEST_SCHEMA, "documents")
HTML_ELEMENTS = RelationName(MATERIAL_SCHEMA, "html_elements")
JSONLD_VALUES = RelationName(MATERIAL_SCHEMA, "jsonld_values")
PAGES = RelationName(MATERIAL_SCHEMA, "pages")
PAGE_OBSERVATIONS = RelationName(MATERIAL_SCHEMA, "page_observations")
LINKS = RelationName(MATERIAL_SCHEMA, "links")
LINK_OBSERVATIONS = RelationName(MATERIAL_SCHEMA, "link_observations")


TABLE_COLUMNS: dict[RelationName, dict[str, ColumnDef]] = {
    CRAWLS: {
        "crawl_id": ColumnDef("UUID", nullable=False),
        "kind": ColumnDef("VARCHAR", nullable=False),
        "graph_id": ColumnDef("UUID"),
        "graph_config_hash": ColumnDef("VARCHAR", nullable=False),
        "graph_config": ColumnDef("VARIANT", nullable=False),
        "root_url_count": ColumnDef("BIGINT", nullable=False),
        "started_at": ColumnDef("TIMESTAMPTZ", nullable=False),
        "finished_at": ColumnDef("TIMESTAMPTZ", nullable=False),
        "stop_reason": ColumnDef("VARCHAR", nullable=False),
    },
    VISITS: {
        "visit_id": ColumnDef("UUID", nullable=False),
        "crawl_id": ColumnDef("UUID", nullable=False),
        "requested_url": ColumnDef("VARCHAR", nullable=False),
        "effective_url": ColumnDef("VARCHAR"),
        "admitted_at": ColumnDef("TIMESTAMPTZ", nullable=False),
        "started_at": ColumnDef("TIMESTAMPTZ"),
        "observed_at": ColumnDef("TIMESTAMPTZ"),
        "finished_at": ColumnDef("TIMESTAMPTZ", nullable=False),
        "outcome": ColumnDef("VARCHAR", nullable=False),
        "status_code": ColumnDef("INTEGER"),
        "document_id": ColumnDef("UUID"),
        "provenance": ColumnDef(
            'STRUCT(kind VARCHAR, "system" VARCHAR, dataset VARCHAR, '
            "source_record_id VARCHAR)",
            nullable=False,
        ),
    },
    ATTEMPTS: {
        "attempt_id": ColumnDef("UUID", nullable=False),
        "visit_id": ColumnDef("UUID", nullable=False),
        "attempt_index": ColumnDef("INTEGER", nullable=False),
        "started_at": ColumnDef("TIMESTAMPTZ", nullable=False),
        "finished_at": ColumnDef("TIMESTAMPTZ", nullable=False),
        "effective_url": ColumnDef("VARCHAR"),
        "status_code": ColumnDef("INTEGER"),
        "outcome": ColumnDef("VARCHAR", nullable=False),
        "failure_stage": ColumnDef("VARCHAR"),
        "failure_code": ColumnDef("VARCHAR"),
        "failure_message": ColumnDef("VARCHAR"),
    },
    STEPS: {
        "attempt_id": ColumnDef("UUID", nullable=False),
        "step_index": ColumnDef("INTEGER", nullable=False),
        "action": ColumnDef("VARCHAR", nullable=False),
        "parameters": ColumnDef("VARIANT", nullable=False),
        "started_at": ColumnDef("TIMESTAMPTZ", nullable=False),
        "duration_ms": ColumnDef("BIGINT", nullable=False),
        "outcome": ColumnDef("VARCHAR", nullable=False),
        "stopping_reason": ColumnDef("VARCHAR"),
        "error_code": ColumnDef("VARCHAR"),
        "error_message": ColumnDef("VARCHAR"),
    },
    DOCUMENTS: {
        "document_id": ColumnDef("UUID", nullable=False),
        "visit_id": ColumnDef("UUID", nullable=False),
        "attempt_id": ColumnDef("UUID"),
        "observed_at": ColumnDef("TIMESTAMPTZ", nullable=False),
        "representation": ColumnDef("VARCHAR", nullable=False),
        "declared_media_type": ColumnDef("VARCHAR"),
        "detected_media_type": ColumnDef("VARCHAR", nullable=False),
        "charset": ColumnDef("VARCHAR"),
        "content_sha256": ColumnDef("VARCHAR", nullable=False),
        "content_bytes": ColumnDef("BIGINT", nullable=False),
        "object_key": ColumnDef("VARCHAR", nullable=False),
        "storage_encoding": ColumnDef("VARCHAR", nullable=False),
        "stored_bytes": ColumnDef("BIGINT", nullable=False),
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
        "observed_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    },
    LINKS: {
        "link_id": ColumnDef("UUID", nullable=False),
        "source_page_id": ColumnDef("UUID", nullable=False),
        "target_page_id": ColumnDef("UUID", nullable=False),
        "source_url": ColumnDef("VARCHAR", nullable=False),
        "target_url": ColumnDef("VARCHAR", nullable=False),
        "relation_scope": ColumnDef("VARCHAR", nullable=False),
    },
    LINK_OBSERVATIONS: {
        "link_id": ColumnDef("UUID", nullable=False),
        "document_id": ColumnDef("UUID", nullable=False),
        "content_sha256": ColumnDef("VARCHAR", nullable=False),
        "element_index": ColumnDef("INTEGER", nullable=False),
        "raw_href": ColumnDef("VARCHAR", nullable=False),
        "observed_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    },
}


PARTITION_BUCKETS = 64


TABLE_LAYOUTS: dict[RelationName, TableLayout] = {
    CRAWLS: TableLayout(
        partition_by=("day(finished_at)",),
        sort_by=("graph_id ASC", "finished_at ASC", "crawl_id ASC"),
    ),
    VISITS: TableLayout(
        partition_by=("day(finished_at)",),
        sort_by=("crawl_id ASC", "admitted_at ASC", "visit_id ASC"),
    ),
    ATTEMPTS: TableLayout(
        partition_by=("day(started_at)",),
        sort_by=("visit_id ASC", "attempt_index ASC"),
    ),
    STEPS: TableLayout(
        partition_by=("day(started_at)",),
        sort_by=("attempt_id ASC", "step_index ASC"),
    ),
    DOCUMENTS: TableLayout(
        partition_by=(f"bucket({PARTITION_BUCKETS}, content_sha256)",),
        sort_by=("content_sha256 ASC", "observed_at ASC", "document_id ASC"),
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
        sort_by=("page_id ASC", "observed_at DESC", "visit_id ASC"),
    ),
    LINKS: TableLayout(
        partition_by=(f"bucket({PARTITION_BUCKETS}, source_page_id)",),
        sort_by=("source_page_id ASC", "target_page_id ASC", "link_id ASC"),
    ),
    LINK_OBSERVATIONS: TableLayout(
        partition_by=(f"bucket({PARTITION_BUCKETS}, document_id)",),
        sort_by=("document_id ASC", "element_index ASC", "link_id ASC"),
    ),
}


TABLE_COMMENTS: dict[RelationName, str] = {
    CRAWLS: "Terminal immutable crawl-graph executions.",
    VISITS: "Terminal destination observations produced by crawls.",
    ATTEMPTS: "Ordered acquisition attempts belonging to visits.",
    STEPS: "Ordered content-completion executions belonging to attempts.",
    DOCUMENTS: "Visit-owned references to immutable document bytes.",
    HTML_ELEMENTS: "Rebuildable structural projections of immutable HTML content.",
    JSONLD_VALUES: "Rebuildable parsed JSON-LD payloads embedded in HTML content.",
    PAGES: "Rebuildable identities for normalized URLs observed through visits.",
    PAGE_OBSERVATIONS: (
        "Rebuildable page-to-visit evidence index for observed documents."
    ),
    LINKS: "Rebuildable normalized source-target pairs positively observed in HTML.",
    LINK_OBSERVATIONS: (
        "Rebuildable document-owned evidence for observed HTML link occurrences."
    ),
}


def _column_comment(relation: RelationName, column: str) -> str:
    return {
        (CRAWLS, "crawl_id"): "Unique identity of the terminal crawl execution.",
        (CRAWLS, "kind"): "Whether Atlas acquired pages or imported external evidence.",
        (CRAWLS, "graph_id"): "Stable logical identity of the crawl graph.",
        (CRAWLS, "graph_config_hash"): "SHA-256 of the canonical frozen graph configuration.",
        (CRAWLS, "graph_config"): "Complete frozen graph configuration.",
        (CRAWLS, "root_url_count"): "Number of root URLs admitted to the initial frontier.",
        (CRAWLS, "started_at"): "Time crawl execution began.",
        (CRAWLS, "finished_at"): "Time crawl execution reached its terminal state.",
        (CRAWLS, "stop_reason"): "Stable reason the crawl stopped.",
        (VISITS, "visit_id"): "Unique identity of this destination observation.",
        (VISITS, "crawl_id"): "Crawl execution that produced this visit.",
        (VISITS, "requested_url"): "Exact URL Atlas attempted to visit.",
        (VISITS, "effective_url"): "Final URL after navigation or redirects, if resolved.",
        (VISITS, "admitted_at"): "Time the destination entered the crawl.",
        (VISITS, "started_at"): "Time acquisition began, if it began.",
        (VISITS, "observed_at"): "Time returned document bytes were captured, if any.",
        (VISITS, "finished_at"): "Time the visit reached its terminal outcome.",
        (VISITS, "outcome"): "Stable terminal logical outcome.",
        (VISITS, "status_code"): "Final HTTP status when available.",
        (VISITS, "document_id"): "Document observation produced by this visit, if any.",
        (VISITS, "provenance"): "Typed origin of this observation.",
        (ATTEMPTS, "attempt_id"): "Unique deterministic identity of this acquisition attempt.",
        (ATTEMPTS, "visit_id"): "Visit that owns this attempt.",
        (ATTEMPTS, "attempt_index"): "Zero-based execution order within the visit.",
        (ATTEMPTS, "started_at"): "Time acquisition work began.",
        (ATTEMPTS, "finished_at"): "Time the attempt reached its terminal outcome.",
        (ATTEMPTS, "effective_url"): "Final URL reached by this attempt, if resolved.",
        (ATTEMPTS, "status_code"): "HTTP status observed by this attempt, if available.",
        (ATTEMPTS, "outcome"): "Stable terminal attempt outcome.",
        (ATTEMPTS, "failure_stage"): "Stable stage that failed, if any.",
        (ATTEMPTS, "failure_code"): "Stable machine-readable failure reason, if any.",
        (ATTEMPTS, "failure_message"): "Bounded diagnostic detail, if needed.",
        (STEPS, "attempt_id"): "Attempt that owns this content-completion execution.",
        (STEPS, "step_index"): "Zero-based execution order within the attempt.",
        (STEPS, "action"): "Controlled content-completion action name.",
        (STEPS, "parameters"): "Complete frozen parameters for this execution.",
        (STEPS, "started_at"): "Time step execution began.",
        (STEPS, "duration_ms"): "Total execution duration in milliseconds.",
        (STEPS, "outcome"): "Stable execution outcome.",
        (STEPS, "stopping_reason"): "Stable reason execution stopped, if applicable.",
        (STEPS, "error_code"): "Stable machine-readable failure reason, if any.",
        (STEPS, "error_message"): "Bounded diagnostic detail, if needed.",
        (DOCUMENTS, "document_id"): "Unique identity of this document observation.",
        (DOCUMENTS, "visit_id"): "Visit that produced this document.",
        (DOCUMENTS, "attempt_id"): "Successful acquisition attempt, if the source retained one.",
        (DOCUMENTS, "observed_at"): "Time the document representation was captured.",
        (DOCUMENTS, "representation"): "Meaning of the retained document bytes.",
        (DOCUMENTS, "declared_media_type"): "Media type claimed by the source, if available.",
        (DOCUMENTS, "detected_media_type"): "Media type detected by Atlas.",
        (DOCUMENTS, "charset"): "Character encoding when meaningful.",
        (DOCUMENTS, "content_sha256"): "Lowercase SHA-256 of uncompressed logical bytes.",
        (DOCUMENTS, "content_bytes"): "Size of uncompressed logical bytes.",
        (DOCUMENTS, "object_key"): "Repository-relative pointer to immutable stored bytes.",
        (DOCUMENTS, "storage_encoding"): "Encoding used for stored bytes.",
        (DOCUMENTS, "stored_bytes"): "Size of the stored object.",
        (HTML_ELEMENTS, "content_sha256"): "Identity of projected immutable HTML bytes.",
        (HTML_ELEMENTS, "element_index"): "Zero-based depth-first document position.",
        (HTML_ELEMENTS, "parent_index"): "Parent element index, null for the root.",
        (HTML_ELEMENTS, "subtree_end_index"): "Exclusive end of this element subtree.",
        (HTML_ELEMENTS, "depth"): "Element depth from the root.",
        (HTML_ELEMENTS, "child_index"): "Zero-based position among element siblings.",
        (HTML_ELEMENTS, "tag"): "Normalized local tag name.",
        (HTML_ELEMENTS, "namespace"): "Normalized element namespace.",
        (HTML_ELEMENTS, "attributes"): "Attribute names and string values.",
        (HTML_ELEMENTS, "text_direct"): "Text directly inside this element before child elements.",
        (HTML_ELEMENTS, "text_tail"): "Text following this element within its parent.",
        (JSONLD_VALUES, "content_sha256"): "Identity of the containing immutable HTML bytes.",
        (JSONLD_VALUES, "element_index"): "Source script element in material.html_elements.",
        (JSONLD_VALUES, "type_terms"): "Distinct raw @type strings found in the payload.",
        (JSONLD_VALUES, "value"): "Complete parsed JSON-LD payload.",
        (PAGES, "page_id"): "Versioned deterministic identity derived from normalized_url.",
        (PAGES, "normalized_url"): "Unique normalized URL represented by this page.",
        (PAGES, "scheme"): "Normalized URL scheme.",
        (PAGES, "hostname"): "Normalized hostname.",
        (PAGES, "port"): "Explicit non-default port, otherwise null.",
        (PAGES, "path"): "Normalized URL path.",
        (PAGES, "query"): "Preserved query string, null when absent.",
        (PAGES, "registrable_domain"): "Public-suffix-aware domain when derivable.",
        (PAGE_OBSERVATIONS, "page_id"): (
            "Deterministic normalized page identity observed by this visit."
        ),
        (PAGE_OBSERVATIONS, "visit_id"): (
            "Visit supplying this page observation and its provenance."
        ),
        (PAGE_OBSERVATIONS, "document_id"): (
            "Document produced by this visit, when one was retained."
        ),
        (PAGE_OBSERVATIONS, "observed_at"): (
            "Time the page representation was captured."
        ),
        (LINKS, "link_id"): (
            "Versioned deterministic identity of the directed normalized page pair."
        ),
        (LINKS, "source_page_id"): (
            "Deterministic identity of the normalized source URL."
        ),
        (LINKS, "target_page_id"): (
            "Deterministic identity of the normalized target URL."
        ),
        (LINKS, "source_url"): "Normalized fragment-free URL where the link was observed.",
        (LINKS, "target_url"): "Normalized fragment-free URL resolved from the observed href.",
        (LINKS, "relation_scope"): "Most-specific deterministic source-target relationship.",
        (LINK_OBSERVATIONS, "link_id"): (
            "Stable link identity supported by this occurrence."
        ),
        (LINK_OBSERVATIONS, "document_id"): (
            "Document observation that owns this link occurrence."
        ),
        (LINK_OBSERVATIONS, "content_sha256"): (
            "Immutable HTML content containing the source anchor."
        ),
        (LINK_OBSERVATIONS, "element_index"): (
            "Source anchor position in material.html_elements."
        ),
        (LINK_OBSERVATIONS, "raw_href"): (
            "Exact non-empty href attribute observed on the source anchor."
        ),
        (LINK_OBSERVATIONS, "observed_at"): (
            "Time the containing document representation was captured."
        ),
    }[(relation, column)]


COLUMN_COMMENTS: dict[RelationName, dict[str, str]] = {
    relation: {
        column: _column_comment(relation, column)
        for column in columns
    }
    for relation, columns in TABLE_COLUMNS.items()
}


def expected_columns() -> dict[RelationName, dict[str, ColumnDef]]:
    return TABLE_COLUMNS
