"""Content-owned complete JSON-LD scripts, parsed once with DuckDB semantics."""
from __future__ import annotations

import duckdb
import pyarrow as pa

from periplus.materialization.document_projection import VisitBatchContext, table_from_rows
from periplus.materialization.registry import PartitionTransform, ProjectionColumn, ProjectionSpec
from periplus.platform.config.duckdb import connection_limits

_SCRIPT_SCHEMA = pa.schema([
    pa.field("content_sha256", pa.string(), nullable=False),
    pa.field("node_index", pa.int32(), nullable=False),
    pa.field("declared_type", pa.string()),
    pa.field("source_text", pa.string(), nullable=False),
])


def project(context: VisitBatchContext) -> pa.Table:
    scripts = table_from_rows(_SCRIPT_SCHEMA, (
        (content_id, element.element_index, element.attributes.get("type"), element.text_direct)
        for content_id in sorted(context.content_output_hashes)
        for element in context.parsed_elements_by_content.get(content_id, ())
        if element.tag.lower() == "script"
        and element.namespace_uri == "http://www.w3.org/1999/xhtml"
    ))
    if not scripts.num_rows:
        return pa.Table.from_batches([], schema=PROJECTION.arrow_schema)
    # Use the public contract's parser, preserving JSON null, duplicate keys,
    # numeric spelling and accepted syntax rather than round-tripping Python JSON.
    with duckdb.connect(config=connection_limits({
        "threads": "1", "memory_limit": "128MB", "max_temp_directory_size": "0B",
    })) as connection:
        connection.register("scripts", scripts)
        result = connection.execute("""
            WITH parsed AS (
                SELECT content_sha256, node_index, source_text,
                       try_cast(source_text AS JSON) AS value
                FROM scripts
                WHERE lower(trim(split_part(declared_type, ';', 1),
                    chr(9) || chr(10) || chr(12) || chr(13) || ' ')) = 'application/ld+json'
            )
            SELECT content_sha256, node_index, value,
                   CASE WHEN value IS NOT NULL THEN NULL
                        WHEN trim(source_text, chr(9) || chr(10) || chr(12) || chr(13) || ' ') = ''
                            THEN 'Empty JSON-LD script'
                        ELSE 'Invalid JSON syntax' END::VARCHAR AS parse_error
            FROM parsed
        """).to_arrow_table()
    return result.cast(PROJECTION.arrow_schema)


PROJECTION = ProjectionSpec(
    name="html_jsonld",
    ownership_grain="content",
    columns=(
        ProjectionColumn("content_sha256", pa.string(), "VARCHAR", "Identity of projected immutable HTML bytes.", False),
        ProjectionColumn("node_index", pa.int32(), "INTEGER", "Source script position in the complete parsed tree.", False),
        ProjectionColumn("value", pa.string(), "JSON", "Complete DuckDB JSON value; SQL null on parse failure."),
        ProjectionColumn("parse_error", pa.string(), "VARCHAR", "Stable parse failure description; null on success."),
    ),
    partitioning=(PartitionTransform("bucket", "content_sha256", buckets=8),),
    sort_order=("content_sha256 ASC", "node_index ASC"),
    projector=project,
    description="Complete embedded JSON-LD scripts, including invalid declarations, without semantic expansion.",
    identity_columns=("content_sha256", "node_index"),
    validation_queries=(
        "SELECT count(*) FROM material.html_jsonld WHERE (value IS NULL) <> (parse_error IS NOT NULL)",
    ),
)
