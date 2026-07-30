CREATE OR REPLACE VIEW web.link_occurrence AS
SELECT
    occurrence.occurrence_id,
    occurrence.link_id,
    occurrence.visit_id,
    link.source_page_id,
    link.target_page_id,
    occurrence.document_id,
    occurrence.content_sha256 AS content_id,
    occurrence.element_index,
    occurrence.observed_at,
    occurrence.raw_href,
    link.target_url AS resolved_url,
    link.relation_scope
FROM material.link_occurrences AS occurrence
JOIN material.links AS link
    USING (link_id);
