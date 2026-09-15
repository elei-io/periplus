SELECT content_id, node_index
FROM public_v1.html_element
WHERE text ILIKE '%robot%'
ORDER BY content_id, node_index;
