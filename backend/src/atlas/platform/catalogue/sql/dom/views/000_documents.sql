CREATE OR REPLACE VIEW dom.documents AS
SELECT
    document.content_sha256 AS content_id,
    document.node_count,
    document.max_depth
FROM material.html_documents AS document;
