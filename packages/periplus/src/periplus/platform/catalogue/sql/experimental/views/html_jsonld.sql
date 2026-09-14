CREATE OR REPLACE VIEW experimental.html_jsonld AS
SELECT content_sha256 AS content_id, node_index, value, parse_error
FROM material.html_jsonld;
