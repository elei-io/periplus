CREATE OR REPLACE MACRO dom.document(
    selected_content_id
) AS TABLE
SELECT
    document.content_sha256 AS content_id,
    document.node_count,
    document.max_depth,
    document.nodes
FROM material.html_documents AS document
WHERE document.content_sha256 = selected_content_id
LIMIT 1;
