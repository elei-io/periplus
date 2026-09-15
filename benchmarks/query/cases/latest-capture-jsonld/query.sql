WITH latest AS (
 SELECT capture_id, document_id, url, captured_at,
        row_number() OVER (PARTITION BY url ORDER BY captured_at DESC, capture_id DESC) AS rn
 FROM public_v1.capture
 WHERE url LIKE '%.gov%' AND document_id >= '0' AND document_id < '1'
)
SELECT c.capture_id, c.url, c.captured_at, j.node_index, j.value, j.parse_error
FROM latest c LEFT JOIN (SELECT * FROM public_v1.html_jsonld WHERE document_id >= '0' AND document_id < '1') j ON j.document_id = c.document_id
WHERE c.rn = 1;
