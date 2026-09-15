SELECT content_id,node_index,tag,text FROM public_v1.html_element
WHERE content_id = 'fixture' AND tag='h1' AND text ILIKE '%the%'
ORDER BY content_id,node_index;
