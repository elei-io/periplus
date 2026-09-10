WITH ranked AS (
 SELECT capture_id, effective_url, captured_at, content_id,
        row_number() OVER (PARTITION BY effective_url ORDER BY captured_at DESC, capture_id DESC) AS rn
 FROM public_v1.capture
 WHERE effective_url LIKE '%.gov%' AND content_id >= '0' AND content_id < '1'
)
SELECT r.capture_id, r.effective_url, r.captured_at, m.node_index,
       left(m.value, 120) AS page_title
FROM ranked r
LEFT JOIN public_v1.html_metadata m ON m.content_id=r.content_id AND m.kind='title'
WHERE r.rn=1
ORDER BY r.captured_at DESC, r.capture_id DESC, m.node_index
LIMIT 20;
