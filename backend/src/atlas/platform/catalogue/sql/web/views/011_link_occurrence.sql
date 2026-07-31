CREATE OR REPLACE VIEW web.link_occurrence AS
SELECT
    occurrence_id AS link_occurrence_id,
    link_id,
    visit_id AS page_visit_id,
    document_id,
    content_sha256 AS content_id,
    element_index,
    observed_at,
    raw_href,
    source_url,
    trim(
        regexp_extract(
            source_url,
            '^https?://(\[[^]]+\]|[^/:]+)',
            1
        ),
        '[]'
    ) AS source_hostname,
    target_url,
    trim(
        regexp_extract(
            target_url,
            '^https?://(\[[^]]+\]|[^/:]+)',
            1
        ),
        '[]'
    ) AS target_hostname,
    relation_scope AS relationship
FROM material.link_occurrences;
