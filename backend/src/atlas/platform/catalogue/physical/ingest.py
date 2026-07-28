"""Typed physical contract for append-only ``ingest.*`` evidence."""

from __future__ import annotations

from atlas.platform.catalogue.physical.base import (
    INGEST_SCHEMA,
    PARTITION_BUCKETS,
    RelationName,
    TableLayout,
)
from atlas.platform.catalogue.schema_types import ColumnDef


CRAWLS = RelationName(INGEST_SCHEMA, "crawls")
VISITS = RelationName(INGEST_SCHEMA, "visits")
ATTEMPTS = RelationName(INGEST_SCHEMA, "attempts")
STEPS = RelationName(INGEST_SCHEMA, "steps")
DOCUMENTS = RelationName(INGEST_SCHEMA, "documents")


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
}


TABLE_LAYOUTS = {
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
}


TABLE_COMMENTS = {
    CRAWLS: "Terminal immutable crawl-graph executions.",
    VISITS: "Terminal destination observations produced by crawls.",
    ATTEMPTS: "Ordered acquisition attempts belonging to visits.",
    STEPS: "Ordered content-completion executions belonging to attempts.",
    DOCUMENTS: "Visit-owned references to immutable document bytes.",
}


COLUMN_COMMENTS = {
    CRAWLS: {
        "crawl_id": "Unique identity of the terminal crawl execution.",
        "kind": "Whether Atlas acquired pages or imported external evidence.",
        "graph_id": "Stable logical identity of the crawl graph.",
        "graph_config_hash": "SHA-256 of the canonical frozen graph configuration.",
        "graph_config": "Complete frozen graph configuration.",
        "root_url_count": "Number of root URLs admitted to the initial frontier.",
        "started_at": "Time crawl execution began.",
        "finished_at": "Time crawl execution reached its terminal state.",
        "stop_reason": "Stable reason the crawl stopped.",
    },
    VISITS: {
        "visit_id": "Unique identity of this destination observation.",
        "crawl_id": "Crawl execution that produced this visit.",
        "requested_url": "Exact URL Atlas attempted to visit.",
        "effective_url": "Final URL after navigation or redirects, if resolved.",
        "admitted_at": "Time the destination entered the crawl.",
        "started_at": "Time acquisition began, if it began.",
        "observed_at": "Time returned document bytes were captured, if any.",
        "finished_at": "Time the visit reached its terminal outcome.",
        "outcome": "Stable terminal logical outcome.",
        "status_code": "Final HTTP status when available.",
        "document_id": "Document observation produced by this visit, if any.",
        "provenance": "Typed origin of this observation.",
    },
    ATTEMPTS: {
        "attempt_id": "Unique deterministic identity of this acquisition attempt.",
        "visit_id": "Visit that owns this attempt.",
        "attempt_index": "Zero-based execution order within the visit.",
        "started_at": "Time acquisition work began.",
        "finished_at": "Time the attempt reached its terminal outcome.",
        "effective_url": "Final URL reached by this attempt, if resolved.",
        "status_code": "HTTP status observed by this attempt, if available.",
        "outcome": "Stable terminal attempt outcome.",
        "failure_stage": "Stable stage that failed, if any.",
        "failure_code": "Stable machine-readable failure reason, if any.",
        "failure_message": "Bounded diagnostic detail, if needed.",
    },
    STEPS: {
        "attempt_id": "Attempt that owns this content-completion execution.",
        "step_index": "Zero-based execution order within the attempt.",
        "action": "Controlled content-completion action name.",
        "parameters": "Complete frozen parameters for this execution.",
        "started_at": "Time step execution began.",
        "duration_ms": "Total execution duration in milliseconds.",
        "outcome": "Stable execution outcome.",
        "stopping_reason": "Stable reason execution stopped, if applicable.",
        "error_code": "Stable machine-readable failure reason, if any.",
        "error_message": "Bounded diagnostic detail, if needed.",
    },
    DOCUMENTS: {
        "document_id": "Unique identity of this document observation.",
        "visit_id": "Visit that produced this document.",
        "attempt_id": "Successful acquisition attempt, if the source retained one.",
        "observed_at": "Time the document representation was captured.",
        "representation": "Meaning of the retained document bytes.",
        "declared_media_type": "Media type claimed by the source, if available.",
        "detected_media_type": "Media type detected by Atlas.",
        "charset": "Character encoding when meaningful.",
        "content_sha256": "Lowercase SHA-256 of uncompressed logical bytes.",
        "content_bytes": "Size of uncompressed logical bytes.",
        "object_key": "Repository-relative pointer to immutable stored bytes.",
        "storage_encoding": "Encoding used for stored bytes.",
        "stored_bytes": "Size of the stored object.",
    },
}
