CREATE OR REPLACE VIEW web.link_occurrence AS
SELECT
    occurrence_id,
    link_id,
    visit_id,
    document_id,
    content_sha256 AS content_id,
    element_index,
    observed_at,
    raw_href,
    source_url,
    target_url,
    relation_scope
FROM material.link_occurrences;
