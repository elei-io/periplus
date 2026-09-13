SELECT content_id, node_index
FROM public_v1.html_element
WHERE text = 'The Requiem Red'
  AND content_id IN (SELECT content_id FROM public_v1.html_term WHERE term = 'requiem')
ORDER BY content_id, node_index;
