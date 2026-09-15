CREATE DATABASE IF NOT EXISTS material;
CREATE TABLE IF NOT EXISTS material.html_documents
(
    document_id FixedString(32),
    content_id FixedString(32),
    representation LowCardinality(String),
    encoding LowCardinality(String),
    document_text String CODEC(ZSTD(3)),
    elements Array(Tuple(
        node_index UInt32, parent_index Nullable(UInt32), subtree_end_index UInt32,
        sibling_index UInt32, depth UInt32, tag String, namespace Nullable(String),
        attributes Map(String, String), text_direct String, text_start UInt64, text_end UInt64
    )) CODEC(ZSTD(3)),
    output_digest FixedString(32)
)
ENGINE = MergeTree ORDER BY document_id;
CREATE TABLE IF NOT EXISTS material.captures
(
    capture_id UUID,
    evidence_digest FixedString(32),
    requested_url String CODEC(ZSTD(3)),
    effective_url Nullable(String) CODEC(ZSTD(3)),
    captured_at Nullable(DateTime64(6, 'UTC')),
    timestamp_precision LowCardinality(String),
    http_status Nullable(UInt16),
    completeness LowCardinality(String),
    content_id Nullable(FixedString(32)),
    document_id Nullable(FixedString(32)),
    byte_length Nullable(UInt64),
    representation LowCardinality(Nullable(String)),
    media_type LowCardinality(Nullable(String)),
    encoding LowCardinality(Nullable(String)),
    object_key Nullable(String),
    storage_encoding LowCardinality(Nullable(String)),
    stored_bytes Nullable(UInt64),
    source_provider LowCardinality(String),
    source_dataset Nullable(String),
    source_record_id Nullable(String),
    archive_record_key String,
    links Array(Tuple(node_index UInt32, raw_href String, target_url String)) CODEC(ZSTD(3)),
    output_digest FixedString(32),
    PROJECTION by_url (SELECT requested_url, captured_at, _part_offset ORDER BY (requested_url, captured_at)),
    PROJECTION by_content (SELECT content_id, _part_offset ORDER BY content_id)
)
ENGINE = MergeTree ORDER BY capture_id;
