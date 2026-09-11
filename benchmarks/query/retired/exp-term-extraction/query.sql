SELECT h.content_id, h.node_index, h.level, h.text
FROM public_v1.html_heading h
WHERE EXISTS (
    SELECT 1 FROM public_v1.prose p
    WHERE p.content_id = h.content_id
      AND regexp_matches(p.text, '\bmonkeys\b')
)
ORDER BY h.content_id, h.node_index
