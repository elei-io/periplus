CREATE DATABASE IF NOT EXISTS material;

CREATE TABLE IF NOT EXISTS material.html_documents
(
    content_sha256 FixedString(32),
    output_sha256 FixedString(32),
    document_text String,
    elements Array(Tuple(
        node_index UInt32,
        parent_index Nullable(UInt32),
        subtree_end_index UInt32,
        sibling_index UInt32,
        depth UInt32,
        tag String,
        namespace Nullable(String),
        attributes Map(String, String),
        text_direct String,
        text_start UInt64,
        text_end UInt64
    )),
    materialized_at DateTime64(6, 'UTC') DEFAULT now64(6)
)
ENGINE = MergeTree
ORDER BY content_sha256;

CREATE TABLE IF NOT EXISTS material.visit_results
(
    visit_id UUID,
    evidence_sha256 FixedString(32),
    output_sha256 FixedString(32),
    html_content_sha256 Nullable(FixedString(32)),
    links Array(Tuple(
        occurrence_id UUID,
        link_id UUID,
        element_index UInt32,
        raw_href String,
        source_url String,
        target_url String,
        relation_scope String
    )),
    materialized_at DateTime64(6, 'UTC') DEFAULT now64(6)
)
ENGINE = MergeTree
ORDER BY visit_id;
