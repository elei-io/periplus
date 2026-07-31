CREATE OR REPLACE VIEW dom.stats AS
SELECT
    content_sha256 AS content_id,
    count(*)::BIGINT AS element_count,
    max(depth)::INTEGER AS max_depth
FROM material.html_elements
GROUP BY content_sha256;
