WITH scoped AS (
    SELECT capture_id, document_id, url
    FROM public_v1.capture
    WHERE url LIKE '%.gov%'
)
SELECT s.capture_id, s.url, h.node_index, h.text
FROM scoped s
LEFT JOIN (SELECT document_id, node_index, substr(tag, 2, 1)::INTEGER AS level, text FROM public_v1.html_element WHERE namespace = 'http://www.w3.org/1999/xhtml' AND tag IN ('h1','h2','h3','h4','h5','h6')) h USING (document_id)
ORDER BY s.capture_id, h.node_index;
