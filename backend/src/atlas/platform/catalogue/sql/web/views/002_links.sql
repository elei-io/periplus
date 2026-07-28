CREATE OR REPLACE VIEW web.links AS
SELECT
    link_id,
    source_page_id,
    target_page_id,
    source_url,
    target_url,
    relation_scope
FROM material.links;
