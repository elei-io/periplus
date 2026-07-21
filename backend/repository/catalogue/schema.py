"""Versioned DuckLake repository schema contract."""

from __future__ import annotations

from ducklake_client import ColumnDef

from dom.schema import ELEMENT_COLUMNS

CATALOGUE_SCHEMA_VERSION = "v0.1.0"
INTERNAL_SCHEMA = "_atlas"
CRAWL_STEPS_TABLE = "crawl_steps"
CRAWL_ATTEMPTS_TABLE = "crawl_attempts"

URL_COLUMNS: dict[str, ColumnDef] = {
    "url_id": ColumnDef("VARCHAR", nullable=False),
    "normalized_url": ColumnDef("VARCHAR", nullable=False),
    "scheme": ColumnDef("VARCHAR", nullable=False),
    "host": ColumnDef("VARCHAR", nullable=False),
    "port": ColumnDef("INTEGER", nullable=False),
    "registrable_domain": ColumnDef("VARCHAR", nullable=False),
    "path": ColumnDef("VARCHAR", nullable=False),
    "query": ColumnDef("VARCHAR", nullable=False),
}

ARTIFACT_COLUMNS: dict[str, ColumnDef] = {
    "artifact_id": ColumnDef("VARCHAR", nullable=False),
    "sha256": ColumnDef("VARCHAR", nullable=False),
    "object_key": ColumnDef("VARCHAR", nullable=False),
    "size_bytes": ColumnDef("BIGINT", nullable=False),
    "created_at": ColumnDef("TIMESTAMPTZ", nullable=False),
}

DOCUMENT_COLUMNS: dict[str, ColumnDef] = {
    "document_id": ColumnDef("VARCHAR", nullable=False),
    "html_sha256": ColumnDef("VARCHAR", nullable=False),
    "html_object_key": ColumnDef("VARCHAR", nullable=False),
    "html_content_type": ColumnDef("VARCHAR", nullable=False),
    "html_encoding": ColumnDef("VARCHAR", nullable=False),
    "html_size_bytes": ColumnDef("BIGINT", nullable=False),
    "html_compressed_size_bytes": ColumnDef("BIGINT", nullable=False),
    "compression": ColumnDef("VARCHAR", nullable=False),
    "dom_schema_version": ColumnDef("INTEGER", nullable=False),
    "parser_name": ColumnDef("VARCHAR", nullable=False),
    "parser_version": ColumnDef("VARCHAR", nullable=False),
    "parser_options_hash": ColumnDef("VARCHAR", nullable=False),
    "element_count": ColumnDef("BIGINT", nullable=False),
    "created_at": ColumnDef("TIMESTAMPTZ", nullable=False),
}

CRAWL_COLUMNS: dict[str, ColumnDef] = {
    "crawl_id": ColumnDef("UUID", nullable=False),
    "document_id": ColumnDef("VARCHAR"),
    "artifact_id": ColumnDef("VARCHAR"),
    "graph_id": ColumnDef("UUID", nullable=False),
    "graph_run_id": ColumnDef("UUID", nullable=False),
    "graph_node_id": ColumnDef("UUID", nullable=False),
    "crawl_request_id": ColumnDef("UUID", nullable=False),
    "source_crawl_id": ColumnDef("UUID"),
    "source_edge_id": ColumnDef("UUID"),
    "requested_url_id": ColumnDef("VARCHAR", nullable=False),
    "final_url_id": ColumnDef("VARCHAR"),
    "captured_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    "status_code": ColumnDef("INTEGER"),
    "duration_ms": ColumnDef("BIGINT"),
    "response_media_type": ColumnDef("VARCHAR"),
    "response_filename": ColumnDef("VARCHAR"),
    "policy_config_hash": ColumnDef("VARCHAR", nullable=False),
    "policy_config_json": ColumnDef("JSON", nullable=False),
    "crawl_policy_id": ColumnDef("UUID"),
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
    "requested_url_id": ColumnDef("VARCHAR", nullable=False),
    "final_url_id": ColumnDef("VARCHAR"),
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

def expected_columns() -> dict[str, dict[str, ColumnDef]]:
    return {
        "urls": URL_COLUMNS,
        "artifacts": ARTIFACT_COLUMNS,
        "documents": DOCUMENT_COLUMNS,
        "crawls": CRAWL_COLUMNS,
        CRAWL_ATTEMPTS_TABLE: CRAWL_ATTEMPT_COLUMNS,
        CRAWL_STEPS_TABLE: CRAWL_STEP_COLUMNS,
        "elements": ELEMENT_COLUMNS,
    }


def expected_internal_columns() -> dict[str, dict[str, ColumnDef]]:
    return {}
