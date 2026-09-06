CREATE OR REPLACE VIEW web.link_occurrence AS
SELECT
    occurrence_id AS link_occurrence_id,
    visit_id AS observation_id,
    content_sha256 AS content_id,
    element_index,
    observed_at,
    source_url,
    raw_href,
    target_url,
    relation_scope
FROM material.link_occurrences;
