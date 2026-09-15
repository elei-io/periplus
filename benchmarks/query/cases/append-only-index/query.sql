SELECT document_id, node_index
FROM public_v1.html_element
WHERE text ILIKE '%robot%'
ORDER BY document_id, node_index;
