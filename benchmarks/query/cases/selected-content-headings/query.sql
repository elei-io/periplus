WITH scoped AS (
    SELECT capture_id, content_id, effective_url
    FROM public_v1.capture
    WHERE effective_url LIKE '%.gov%'
)
SELECT s.capture_id, s.effective_url, h.node_index, h.text
FROM scoped s
LEFT JOIN public_v1.html_heading h USING (content_id)
ORDER BY s.capture_id, h.node_index;
