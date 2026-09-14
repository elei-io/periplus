SELECT h.level, h.text, c.effective_url AS url
FROM (SELECT content_id, node_index, substr(tag, 2, 1)::INTEGER AS level, text FROM public_v1.html_element WHERE namespace = 'http://www.w3.org/1999/xhtml' AND tag IN ('h1','h2','h3','h4','h5','h6')) h
JOIN public_v1.capture c USING (content_id)
WHERE page_url ILIKE '%books.%'
  AND h.text ILIKE '%Light%';
