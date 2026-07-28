CREATE OR REPLACE VIEW web.link_observations AS
SELECT
    link_id,
    document_id,
    content_sha256 AS content_id,
    element_index,
    raw_href,
    observed_at
FROM material.link_observations;
