SELECT h.level, h.text, c.effective_url AS url
FROM public_v1.html_heading h
JOIN public_v1.capture c USING (content_id)
WHERE requested_url ILIKE '%books.%'
  AND h.text ILIKE '%Light%';
