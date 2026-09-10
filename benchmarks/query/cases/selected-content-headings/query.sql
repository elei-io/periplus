WITH scoped AS (
    SELECT capture_id, content_id, effective_url
    FROM public_v1.capture
    WHERE effective_url LIKE '%.gov%'
)
SELECT s.capture_id, s.effective_url, h.node_index, h.text,
       left(p.text, 200) AS body_prefix
FROM scoped s
JOIN public_v1.prose p USING (content_id)
LEFT JOIN public_v1.html_heading h USING (content_id)
ORDER BY s.capture_id, h.node_index;
