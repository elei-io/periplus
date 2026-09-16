CREATE DATABASE IF NOT EXISTS material;
CREATE TABLE IF NOT EXISTS material.html_documents
(
    document_id String,
    content_id FixedString(32),
    representation LowCardinality(String),
    encoding LowCardinality(String),
    document_text String CODEC(ZSTD(3)),
    element_count UInt64,
    INDEX words lower(document_text) TYPE text(tokenizer=splitByNonAlpha) GRANULARITY 1,
    output_digest FixedString(32)
)
ENGINE = MergeTree ORDER BY document_id SETTINGS min_bytes_for_wide_part=268435456;
CREATE TABLE IF NOT EXISTS material.captures
(
    capture_id UUID,
    evidence_digest FixedString(32),
    requested_url String CODEC(ZSTD(3)),
    url String CODEC(ZSTD(3)),
    effective_url Nullable(String) CODEC(ZSTD(3)),
    captured_at Nullable(DateTime64(6, 'UTC')),
    timestamp_precision LowCardinality(String),
    http_status Nullable(UInt16),
    completeness LowCardinality(String),
    content_id Nullable(FixedString(32)),
    document_id Nullable(String),
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
    INDEX url_exact url TYPE text(tokenizer=array) GRANULARITY 1,
    INDEX document_exact document_id TYPE text(tokenizer=array) GRANULARITY 1
)
ENGINE = MergeTree ORDER BY capture_id SETTINGS min_bytes_for_wide_part=268435456;

CREATE TABLE IF NOT EXISTS material.html_elements
(
    document_id String,
    node_index UInt32,
    parent_index Nullable(UInt32),
    subtree_end_index UInt32,
    sibling_index UInt32,
    depth UInt32,
    tag LowCardinality(String),
    namespace Nullable(String),
    attributes Map(String, String) CODEC(ZSTD(3)),
    text_direct String CODEC(ZSTD(3)),
    text_start UInt64,
    text_end UInt64,
    text String CODEC(ZSTD(3)),
    output_digest FixedString(32),
    INDEX classes splitByWhitespace(attributes['class']) TYPE text(tokenizer=array) GRANULARITY 1,
    INDEX attribute_keys mapKeys(attributes) TYPE text(tokenizer=array) GRANULARITY 1,
    INDEX attribute_values mapValues(attributes) TYPE text(tokenizer=array) GRANULARITY 1,
    INDEX direct_words lower(text_direct) TYPE text(tokenizer=splitByNonAlpha) GRANULARITY 1,
    INDEX subtree_words lower(text) TYPE text(tokenizer=splitByNonAlpha) GRANULARITY 1
)
ENGINE = MergeTree ORDER BY (document_id, node_index)
SETTINGS min_bytes_for_wide_part=268435456;

CREATE TABLE IF NOT EXISTS material.json_ld
(
    document_id String,
    node_index UInt32,
    json String CODEC(ZSTD(3)),
    types Array(String),
    name String CODEC(ZSTD(3)),
    output_digest FixedString(32),
    INDEX type_exact types TYPE text(tokenizer=array) GRANULARITY 1,
    INDEX name_exact name TYPE text(tokenizer=array) GRANULARITY 1
)
ENGINE = MergeTree ORDER BY (document_id, node_index)
SETTINGS min_bytes_for_wide_part=268435456;
