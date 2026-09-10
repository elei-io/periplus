WITH latest AS (
 SELECT capture_id, content_id, effective_url, captured_at,
        row_number() OVER (PARTITION BY effective_url ORDER BY captured_at DESC, capture_id DESC) AS rn
 FROM public_v1.capture
 WHERE effective_url LIKE '%.gov%' AND content_id >= '0' AND content_id < '1'
)
SELECT c.capture_id, c.effective_url, c.captured_at, j.node_index, j.value, j.parse_error
FROM latest c LEFT JOIN (SELECT * FROM public_v1.html_jsonld WHERE content_id >= '0' AND content_id < '1') j ON j.content_id = c.content_id
WHERE c.rn = 1;
