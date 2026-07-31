CREATE OR REPLACE VIEW web.link AS
SELECT
    link_id,
    source_url,
    target_url,
    relation_scope,
    min(observed_at) AS first_seen_at,
    max(observed_at) AS last_seen_at,
    count(DISTINCT visit_id) AS visit_count,
    count(DISTINCT content_sha256) AS distinct_content_count,
    count(*) AS occurrence_count
FROM material.link_occurrences
GROUP BY link_id, source_url, target_url, relation_scope;
