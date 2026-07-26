"""Versioned DuckLake repository schema contract.

The human-readable canonical contract lives in ``LAKE_SCHEMA.md`` at the
repository root. Keep every table, column, comment, partition, and sort
definition synchronized with that document.
"""

from __future__ import annotations

from dataclasses import dataclass

from dom.schema import ELEMENT_COLUMNS
from schema_types import ColumnDef

CATALOGUE_SCHEMA_VERSION = "1.0.0"
POLICY_SCHEMA_VERSION = 1
INTERNAL_SCHEMA = "_atlas"
CRAWL_STEPS_TABLE = "crawl_steps"
CRAWL_ATTEMPTS_TABLE = "crawl_attempts"


@dataclass(frozen=True, slots=True)
class TableLayout:
    partition_by: tuple[str, ...] = ()
    sort_by: tuple[str, ...] = ()


ARTIFACT_COLUMNS: dict[str, ColumnDef] = {
    "artifact_id": ColumnDef("VARCHAR", nullable=False),
    "object_key": ColumnDef("VARCHAR", nullable=False),
    "size_bytes": ColumnDef("BIGINT", nullable=False),
    "response_media_type": ColumnDef("VARCHAR", nullable=False),
    "detected_media_type": ColumnDef("VARCHAR", nullable=False),
    "detector_name": ColumnDef("VARCHAR", nullable=False),
    "detector_version": ColumnDef("VARCHAR", nullable=False),
    "detection_confidence": ColumnDef("DOUBLE", nullable=False),
    "first_seen_at": ColumnDef("TIMESTAMPTZ", nullable=False),
}

DOCUMENT_COLUMNS: dict[str, ColumnDef] = {
    "document_id": ColumnDef("VARCHAR", nullable=False),
    "object_key": ColumnDef("VARCHAR", nullable=False),
    "content_type": ColumnDef("VARCHAR", nullable=False),
    "encoding": ColumnDef("VARCHAR", nullable=False),
    "size_bytes": ColumnDef("BIGINT", nullable=False),
    "compressed_size_bytes": ColumnDef("BIGINT", nullable=False),
    "compression": ColumnDef("VARCHAR", nullable=False),
    "dom_schema_version": ColumnDef("INTEGER", nullable=False),
    "parser_name": ColumnDef("VARCHAR", nullable=False),
    "parser_version": ColumnDef("VARCHAR", nullable=False),
    "parser_options_hash": ColumnDef("VARCHAR", nullable=False),
    "element_count": ColumnDef("BIGINT", nullable=False),
    "first_seen_at": ColumnDef("TIMESTAMPTZ", nullable=False),
}

CRAWL_COLUMNS: dict[str, ColumnDef] = {
    "crawl_id": ColumnDef("UUID", nullable=False),
    "document_id": ColumnDef("VARCHAR"),
    "artifact_id": ColumnDef("VARCHAR"),
    "graph_id": ColumnDef("UUID", nullable=False),
    "graph_run_id": ColumnDef("UUID", nullable=False),
    "graph_node_id": ColumnDef("UUID", nullable=False),
    "source_crawl_id": ColumnDef("UUID"),
    "source_edge_id": ColumnDef("UUID"),
    "requested_url": ColumnDef("VARCHAR", nullable=False),
    "url": ColumnDef("VARCHAR", nullable=False),
    "scheme": ColumnDef("VARCHAR", nullable=False),
    "host": ColumnDef("VARCHAR", nullable=False),
    "port": ColumnDef("INTEGER", nullable=False),
    "registrable_domain": ColumnDef("VARCHAR", nullable=False),
    "path": ColumnDef("VARCHAR", nullable=False),
    "query": ColumnDef("VARCHAR", nullable=False),
    "started_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    "completed_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    "content_captured_at": ColumnDef("TIMESTAMPTZ"),
    "status_code": ColumnDef("INTEGER"),
    "response_media_type": ColumnDef("VARCHAR"),
    "policy_schema_version": ColumnDef("INTEGER", nullable=False),
    "effective_policy_hash": ColumnDef("VARCHAR", nullable=False),
    "effective_policy": ColumnDef("JSON", nullable=False),
    "outcome": ColumnDef("VARCHAR", nullable=False),
    "failure_code": ColumnDef("VARCHAR"),
    "failure_stage": ColumnDef("VARCHAR"),
    "failure_retryable": ColumnDef("BOOLEAN"),
    "failure_detail": ColumnDef("VARCHAR"),
}

CRAWL_ATTEMPT_COLUMNS: dict[str, ColumnDef] = {
    "crawl_id": ColumnDef("UUID", nullable=False),
    "attempt_number": ColumnDef("INTEGER", nullable=False),
    "started_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    "completed_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    "requested_url": ColumnDef("VARCHAR", nullable=False),
    "url": ColumnDef("VARCHAR", nullable=False),
    "status_code": ColumnDef("INTEGER"),
    "response_media_type": ColumnDef("VARCHAR"),
    "outcome": ColumnDef("VARCHAR", nullable=False),
    "failure_code": ColumnDef("VARCHAR"),
    "retry_after_seconds": ColumnDef("DOUBLE"),
}

CRAWL_STEP_COLUMNS: dict[str, ColumnDef] = {
    "crawl_id": ColumnDef("UUID", nullable=False),
    "attempt_number": ColumnDef("INTEGER", nullable=False),
    "step_ordinal": ColumnDef("INTEGER", nullable=False),
    "method": ColumnDef("VARCHAR", nullable=False),
    "method_version": ColumnDef("INTEGER", nullable=False),
    "config_hash": ColumnDef("VARCHAR", nullable=False),
    "config_json": ColumnDef("JSON", nullable=False),
    "started_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    "duration_ms": ColumnDef("BIGINT", nullable=False),
    "iterations": ColumnDef("INTEGER", nullable=False),
    "stop_reason": ColumnDef("VARCHAR", nullable=False),
    "before_element_count": ColumnDef("BIGINT", nullable=False),
    "after_element_count": ColumnDef("BIGINT", nullable=False),
    "before_text_chars": ColumnDef("BIGINT", nullable=False),
    "after_text_chars": ColumnDef("BIGINT", nullable=False),
    "before_link_count": ColumnDef("BIGINT", nullable=False),
    "after_link_count": ColumnDef("BIGINT", nullable=False),
    "before_scroll_height": ColumnDef("BIGINT", nullable=False),
    "after_scroll_height": ColumnDef("BIGINT", nullable=False),
}

TABLE_LAYOUTS: dict[str, TableLayout] = {
    "crawls": TableLayout(
        partition_by=("day(completed_at)",),
        sort_by=(
            "registrable_domain ASC",
            "host ASC",
            "path ASC",
            "completed_at ASC",
            "crawl_id ASC",
        ),
    ),
    CRAWL_ATTEMPTS_TABLE: TableLayout(
        partition_by=("day(started_at)",),
        sort_by=("crawl_id ASC", "attempt_number ASC"),
    ),
    CRAWL_STEPS_TABLE: TableLayout(
        partition_by=("day(started_at)",),
        sort_by=("crawl_id ASC", "attempt_number ASC", "step_ordinal ASC"),
    ),
    "documents": TableLayout(
        partition_by=("bucket(64, document_id)",),
        sort_by=("document_id ASC",),
    ),
    "elements": TableLayout(
        partition_by=("bucket(64, document_id)",),
        sort_by=("document_id ASC", "element_index ASC"),
    ),
    "artifacts": TableLayout(sort_by=("artifact_id ASC",)),
}

TABLE_STABLE_KEYS: dict[str, tuple[str, ...]] = {
    "crawls": ("crawl_id",),
    CRAWL_ATTEMPTS_TABLE: ("crawl_id", "attempt_number"),
    CRAWL_STEPS_TABLE: ("crawl_id", "attempt_number", "step_ordinal"),
    "documents": ("document_id",),
    "elements": ("document_id", "element_index"),
    "artifacts": ("artifact_id",),
}

# (local columns, target table, target columns, optional)
TABLE_RELATIONSHIPS: dict[
    str,
    tuple[tuple[tuple[str, ...], str, tuple[str, ...], bool], ...],
] = {
    "crawls": (
        (("document_id",), "documents", ("document_id",), True),
        (("artifact_id",), "artifacts", ("artifact_id",), True),
    ),
    CRAWL_ATTEMPTS_TABLE: (
        (("crawl_id",), "crawls", ("crawl_id",), False),
    ),
    CRAWL_STEPS_TABLE: (
        (
            ("crawl_id", "attempt_number"),
            CRAWL_ATTEMPTS_TABLE,
            ("crawl_id", "attempt_number"),
            False,
        ),
    ),
    "elements": (
        (("document_id",), "documents", ("document_id",), False),
    ),
}

TABLE_COMMENTS: dict[str, str] = {
    "crawls": (
        "Immutable logical page-acquisition results with self-describing effective "
        "URL, frozen policy, terminal outcome, and graph provenance."
    ),
    CRAWL_ATTEMPTS_TABLE: (
        "Ordered network and navigation attempt evidence for logical crawls, "
        "including retry failures that preceded terminal success or failure."
    ),
    CRAWL_STEPS_TABLE: (
        "Ordered per-attempt evidence for dynamic waiting, fixed waiting, scrolling, "
        "and expansion content-completion methods."
    ),
    "documents": (
        "Immutable content-addressed retained HTML documents and their active "
        "structural DOM projection recipe."
    ),
    "elements": (
        "Versioned structural DOM projection stored in depth-first document order "
        "and bucketed by document identity."
    ),
    "artifacts": (
        "Immutable content-addressed retained non-HTML response artifacts with "
        "media-type detection evidence."
    ),
}

COLUMN_COMMENTS: dict[str, dict[str, str]] = {
    "crawls": {
        "crawl_id": "Stable logical crawl identity and the parent identity used by attempt and completion-step evidence.",
        "document_id": "Content-addressed retained HTML document identity; null when the crawl retained no HTML document.",
        "artifact_id": "Content-addressed retained non-HTML artifact identity; null when the crawl retained no artifact.",
        "graph_id": "Identity of the crawl graph whose frozen run admitted this crawl.",
        "graph_run_id": "Identity of the frozen graph run that admitted this crawl.",
        "graph_node_id": "Identity of the graph node that acquired this URL.",
        "source_crawl_id": "Prior crawl whose outgoing edge discovered this crawl, or null for a root crawl.",
        "source_edge_id": "Graph edge that admitted this crawl, or null for a root crawl.",
        "requested_url": "Normalized absolute HTTP or HTTPS URL Atlas was asked to acquire.",
        "url": "Normalized effective URL after navigation; equals requested_url when no different final URL was observed.",
        "scheme": "Lowercase scheme of the effective URL.",
        "host": "Lowercase host of the effective URL.",
        "port": "Effective URL port, including the default port derived from the scheme.",
        "registrable_domain": "Registrable domain of the effective URL, or the host itself for an IP address or suffixless host.",
        "path": "Normalized effective URL path, always beginning with a slash.",
        "query": "Effective URL query without a leading question mark; empty when absent.",
        "started_at": "Start time of the first acquisition attempt belonging to this logical crawl.",
        "completed_at": "Terminal time of the logical crawl after success, skip, or failure.",
        "content_captured_at": "Time retained response bytes were finalized; null when no document or artifact was captured.",
        "status_code": "Terminal HTTP response status when one was obtained.",
        "response_media_type": "Terminal accepted or observed response media type when one was obtained.",
        "policy_schema_version": "Version of the durable effective_policy JSON contract.",
        "effective_policy_hash": "Lowercase SHA-256 digest of the canonical effective_policy JSON representation.",
        "effective_policy": "Complete frozen effective crawl and domain policy used by this crawl.",
        "outcome": "Logical crawl outcome: success, skipped, or failed.",
        "failure_code": "Stable typed terminal failure code; null unless outcome is failed.",
        "failure_stage": "Stable acquisition stage where the terminal failure occurred; null unless outcome is failed.",
        "failure_retryable": "Whether the terminal cause was classified as retryable when recorded; null unless outcome is failed.",
        "failure_detail": "Bounded instance-specific terminal diagnostic; null unless outcome is failed.",
    },
    CRAWL_ATTEMPTS_TABLE: {
        "crawl_id": "Logical crawl identity that owns this attempt.",
        "attempt_number": "One-based attempt ordinal within the logical crawl.",
        "started_at": "Time this attempt began.",
        "completed_at": "Time this attempt completed.",
        "requested_url": "Normalized absolute HTTP or HTTPS URL used to begin this attempt.",
        "url": "Normalized effective URL observed by this attempt, or requested_url when no different final URL was observed.",
        "status_code": "HTTP response status obtained by this attempt, if any.",
        "response_media_type": "Response media type observed by this attempt, if any.",
        "outcome": "Attempt outcome: success, retry, skipped, or failed.",
        "failure_code": "Stable typed attempt failure code, or null when the attempt did not fail.",
        "retry_after_seconds": "Server-requested retry delay in seconds, or null when absent.",
    },
    CRAWL_STEPS_TABLE: {
        "crawl_id": "Logical crawl identity that owns this completion step.",
        "attempt_number": "One-based parent attempt ordinal.",
        "step_ordinal": "One-based completion-step ordinal within the attempt.",
        "method": "Completion method: wait_dynamic, wait_fixed, scroll, or expand.",
        "method_version": "Version of the completion-method evidence contract.",
        "config_hash": "Lowercase SHA-256 digest of the canonical config_json representation.",
        "config_json": "Frozen effective configuration used for this completion step.",
        "started_at": "Time this completion step began.",
        "duration_ms": "Elapsed completion-step duration in milliseconds.",
        "iterations": "Number of bounded method iterations performed.",
        "stop_reason": "Stable reason the completion method stopped.",
        "before_element_count": "DOM element count observed before the completion step.",
        "after_element_count": "DOM element count observed after the completion step.",
        "before_text_chars": "DOM text character count observed before the completion step.",
        "after_text_chars": "DOM text character count observed after the completion step.",
        "before_link_count": "HTTP or HTTPS anchor count observed before the completion step.",
        "after_link_count": "HTTP or HTTPS anchor count observed after the completion step.",
        "before_scroll_height": "Document scroll height observed before the completion step.",
        "after_scroll_height": "Document scroll height observed after the completion step.",
    },
    "documents": {
        "document_id": "Algorithm-qualified content identity in sha256:<lowercase hexadecimal digest> form.",
        "object_key": "Repository-relative key of the immutable compressed HTML object.",
        "content_type": "Stored HTML or XHTML media type.",
        "encoding": "Character encoding used for the canonical retained HTML text.",
        "size_bytes": "Uncompressed canonical HTML size in bytes.",
        "compressed_size_bytes": "Compressed immutable object size in bytes.",
        "compression": "Compression format used by the immutable repository object.",
        "dom_schema_version": "Version of the elements table structural projection contract.",
        "parser_name": "Parser implementation used for the active structural projection.",
        "parser_version": "Parser implementation version used for the active structural projection.",
        "parser_options_hash": "Lowercase SHA-256 digest of the canonical parser options.",
        "element_count": "Expected number of elements rows in the active structural projection.",
        "first_seen_at": "Time this content identity was first committed to DuckLake.",
    },
    "elements": {
        "document_id": "Content-addressed parent document identity.",
        "element_index": "Zero-based depth-first element ordinal within the document.",
        "parent_index": "Element index of the parent element, or null for the document element.",
        "subtree_end_index": "Inclusive final element index in this element subtree.",
        "depth": "Zero-based element depth, with the document element at depth zero.",
        "tag": "Normalized element tag name.",
        "namespace_uri": "Element namespace URI, or null when absent.",
        "attributes": "Map of parsed element attribute names to values.",
        "text_direct": "Character data directly inside the element before its first child.",
        "text_tail": "Character data immediately following the element in its parent.",
    },
    "artifacts": {
        "artifact_id": "Algorithm-qualified content identity in sha256:<lowercase hexadecimal digest> form.",
        "object_key": "Repository-relative key of the immutable artifact object.",
        "size_bytes": "Immutable artifact size in bytes.",
        "response_media_type": "Response media type declared by the acquisition response.",
        "detected_media_type": "Media type detected from the retained artifact bytes.",
        "detector_name": "Media-type detector implementation.",
        "detector_version": "Media-type detector implementation version.",
        "detection_confidence": "Detector confidence from zero through one.",
        "first_seen_at": "Time this content identity was first committed to DuckLake.",
    },
}


def expected_columns() -> dict[str, dict[str, ColumnDef]]:
    return {
        "artifacts": ARTIFACT_COLUMNS,
        "documents": DOCUMENT_COLUMNS,
        "crawls": CRAWL_COLUMNS,
        CRAWL_ATTEMPTS_TABLE: CRAWL_ATTEMPT_COLUMNS,
        CRAWL_STEPS_TABLE: CRAWL_STEP_COLUMNS,
        "elements": ELEMENT_COLUMNS,
    }


def expected_internal_columns() -> dict[str, dict[str, ColumnDef]]:
    return {}
