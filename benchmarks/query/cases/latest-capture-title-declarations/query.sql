WITH ranked AS (
 SELECT capture_id, url, captured_at, document_id,
        row_number() OVER (PARTITION BY url ORDER BY captured_at DESC, capture_id DESC) AS rn
 FROM public_v1.capture
 WHERE url LIKE '%.gov%' AND document_id >= '0' AND document_id < '1'
)
SELECT r.capture_id, r.url, r.captured_at, m.node_index,
       left(m.value, 120) AS page_title
FROM ranked r
LEFT JOIN (SELECT * FROM public_v1.html_metadata WHERE document_id >= '0' AND document_id < '1') m ON m.document_id=r.document_id AND m.source='title'
WHERE r.rn=1
ORDER BY r.captured_at DESC, r.capture_id DESC, m.node_index
LIMIT 20;
