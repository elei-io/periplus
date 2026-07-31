CREATE OR REPLACE VIEW web.link AS
WITH retained_link AS (
SELECT
    link_id,
    source_url,
    target_url,
    relation_scope AS relationship,
    min(observed_at) AS first_seen_at,
    max(observed_at) AS last_seen_at,
    count(DISTINCT visit_id) AS page_visit_count,
    count(DISTINCT content_sha256) AS content_count,
    count(*) AS occurrence_count
FROM material.link_occurrences
GROUP BY link_id, source_url, target_url, relation_scope
)
SELECT
    link_id,
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
    relationship,
    first_seen_at,
    last_seen_at,
    page_visit_count,
    content_count,
    occurrence_count
FROM retained_link;
