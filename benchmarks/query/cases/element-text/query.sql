SELECT document_id,node_index,tag,text FROM public_v1.html_element
WHERE document_id = 'fixture' AND tag='h1' AND text ILIKE '%the%'
ORDER BY document_id,node_index;
