"""Versioned DuckLake repository schema contract."""

from __future__ import annotations

from ducklake_client import ColumnDef

from dom.schema import ELEMENT_COLUMNS

CATALOGUE_SCHEMA_VERSION = 8

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
    "graph_id": ColumnDef("UUID", nullable=False),
    "graph_run_id": ColumnDef("UUID", nullable=False),
    "graph_node_id": ColumnDef("UUID", nullable=False),
    "crawl_request_id": ColumnDef("UUID", nullable=False),
    "source_crawl_id": ColumnDef("UUID"),
    "source_edge_id": ColumnDef("UUID"),
    "requested_url": ColumnDef("VARCHAR", nullable=False),
    "normalized_url": ColumnDef("VARCHAR", nullable=False),
    "final_url": ColumnDef("VARCHAR"),
    "page_url": ColumnDef("VARCHAR", nullable=False),
    "url_scheme": ColumnDef("VARCHAR", nullable=False),
    "url_host": ColumnDef("VARCHAR", nullable=False),
    "url_port": ColumnDef("INTEGER", nullable=False),
    "url_registrable_domain": ColumnDef("VARCHAR", nullable=False),
    "url_path": ColumnDef("VARCHAR", nullable=False),
    "url_query": ColumnDef("VARCHAR", nullable=False),
    "captured_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    "status_code": ColumnDef("INTEGER"),
    "duration_ms": ColumnDef("BIGINT"),
    "input_json": ColumnDef("JSON", nullable=False),
    "input_hash": ColumnDef("VARCHAR", nullable=False),
    "crawl_policy_id": ColumnDef("UUID"),
    "crawl_policy_revision": ColumnDef("INTEGER"),
    "warnings_json": ColumnDef("JSON", nullable=False),
    "errors_json": ColumnDef("JSON", nullable=False),
}

MATERIALIZATION_SCOPE_RESULT_COLUMNS: dict[str, ColumnDef] = {
    "materialization_id": ColumnDef("UUID", nullable=False),
    "definition_revision_id": ColumnDef("UUID", nullable=False),
    "scope_kind": ColumnDef("VARCHAR", nullable=False),
    "scope_id": ColumnDef("VARCHAR", nullable=False),
    "operation_id": ColumnDef("VARCHAR", nullable=False),
    "row_count": ColumnDef("BIGINT", nullable=False),
    "output_bytes": ColumnDef("BIGINT", nullable=False),
    "status": ColumnDef("VARCHAR", nullable=False),
    "error": ColumnDef("VARCHAR"),
    "started_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    "completed_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    "partition_value": ColumnDef("DATE"),
}

CRAWL_MATERIALIZATION_FANOUT_COLUMNS: dict[str, ColumnDef] = {
    "crawl_id": ColumnDef("UUID", nullable=False),
    "planning_completed_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    "triggered_count": ColumnDef("BIGINT", nullable=False),
    "settled_count": ColumnDef("BIGINT", nullable=False),
    "failed_count": ColumnDef("BIGINT", nullable=False),
    "completed_at": ColumnDef("TIMESTAMPTZ"),
}

CRAWL_MATERIALIZATION_FANOUT_MEMBER_COLUMNS: dict[str, ColumnDef] = {
    "crawl_id": ColumnDef("UUID", nullable=False),
    "materialization_id": ColumnDef("UUID", nullable=False),
    "definition_revision_id": ColumnDef("UUID", nullable=False),
    "scope_kind": ColumnDef("VARCHAR", nullable=False),
    "scope_id": ColumnDef("VARCHAR", nullable=False),
    "status": ColumnDef("VARCHAR", nullable=False),
    "settled_at": ColumnDef("TIMESTAMPTZ"),
    "error": ColumnDef("VARCHAR"),
}

def expected_columns() -> dict[str, dict[str, ColumnDef]]:
    return {
        "documents": DOCUMENT_COLUMNS,
        "crawls": CRAWL_COLUMNS,
        "elements": ELEMENT_COLUMNS,
        "materialization_scope_results": MATERIALIZATION_SCOPE_RESULT_COLUMNS,
        "crawl_materialization_fanouts": CRAWL_MATERIALIZATION_FANOUT_COLUMNS,
        "crawl_materialization_fanout_members": CRAWL_MATERIALIZATION_FANOUT_MEMBER_COLUMNS,
    }
