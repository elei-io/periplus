"""Versioned DuckLake repository schema contract."""

from __future__ import annotations

from ducklake_client import ColumnDef

from dom.schema import ELEMENT_COLUMNS

CATALOGUE_SCHEMA_VERSION = 4

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
    "run_id": ColumnDef("UUID", nullable=False),
    "task_id": ColumnDef("UUID", nullable=False),
    "task_revision": ColumnDef("INTEGER", nullable=False),
    "primitive": ColumnDef("VARCHAR", nullable=False),
    "requested_url": ColumnDef("VARCHAR", nullable=False),
    "normalized_url": ColumnDef("VARCHAR", nullable=False),
    "final_url": ColumnDef("VARCHAR"),
    "captured_at": ColumnDef("TIMESTAMPTZ", nullable=False),
    "status_code": ColumnDef("INTEGER"),
    "duration_ms": ColumnDef("BIGINT"),
    "input_json": ColumnDef("JSON", nullable=False),
    "input_hash": ColumnDef("VARCHAR", nullable=False),
    "crawl_policy_id": ColumnDef("UUID"),
    "crawl_policy_revision": ColumnDef("INTEGER"),
    "data_schema_id": ColumnDef("UUID"),
    "query_schema_id": ColumnDef("UUID"),
    "warnings_json": ColumnDef("JSON", nullable=False),
    "errors_json": ColumnDef("JSON", nullable=False),
}

RUN_MANIFEST_COLUMNS: dict[str, ColumnDef] = {
    "run_id": ColumnDef("UUID", nullable=False),
    "task_id": ColumnDef("UUID", nullable=False),
    "task_revision": ColumnDef("INTEGER", nullable=False),
    "primitive": ColumnDef("VARCHAR", nullable=False),
    "input_json": ColumnDef("JSON", nullable=False),
    "queued_at": ColumnDef("TIMESTAMPTZ", nullable=False),
}

RUN_CRAWL_USAGE_COLUMNS: dict[str, ColumnDef] = {
    "usage_id": ColumnDef("UUID", nullable=False),
    "run_id": ColumnDef("UUID", nullable=False),
    "crawl_id": ColumnDef("UUID", nullable=False),
    "document_id": ColumnDef("VARCHAR"),
    "requested_url": ColumnDef("VARCHAR", nullable=False),
    "normalized_url": ColumnDef("VARCHAR", nullable=False),
    "source": ColumnDef("VARCHAR", nullable=False),
    "role": ColumnDef("VARCHAR", nullable=False),
    "ordinal": ColumnDef("BIGINT", nullable=False),
    "returned": ColumnDef("BOOLEAN", nullable=False),
}


def expected_columns() -> dict[str, dict[str, ColumnDef]]:
    return {
        "documents": DOCUMENT_COLUMNS,
        "crawls": CRAWL_COLUMNS,
        "elements": ELEMENT_COLUMNS,
        "run_manifests": RUN_MANIFEST_COLUMNS,
        "run_crawl_usages": RUN_CRAWL_USAGE_COLUMNS,
    }
