CREATE OR REPLACE VIEW web.link AS
SELECT
    link.link_id,
    link.source_page_id,
    link.target_page_id,
    link.source_url,
    link.target_url,
    link.relation_scope,
    link.first_seen_at,
    link.last_seen_at,
    link.visit_count,
    link.distinct_content_count,
    link.occurrence_count
FROM material.links AS link;
