SELECT h.level, h.text, c.effective_url AS url
FROM html_heading h
JOIN capture c USING (content_id)
WHERE requested_url ILIKE '%books.%'
  AND h.text ILIKE '%Light%';
