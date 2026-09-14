SELECT c.effective_url, c.captured_at, h.text AS heading, s.*
FROM public_v1.capture c JOIN public_v1.html_heading h USING(content_id)
JOIN public_v1.html_section s
 ON s.content_id=h.content_id AND s.heading_node_index=h.node_index
WHERE c.effective_url LIKE '%.gov%'
 AND lower(h.text) LIKE '%artificial intelligence%'
ORDER BY c.captured_at DESC;
