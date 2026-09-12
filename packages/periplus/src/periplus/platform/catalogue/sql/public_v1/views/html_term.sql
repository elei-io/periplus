CREATE OR REPLACE VIEW public_v1.html_term AS
SELECT term, content_sha256 AS content_id, node_indexes
FROM material.html_terms;
