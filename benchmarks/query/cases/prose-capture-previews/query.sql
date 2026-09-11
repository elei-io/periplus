SELECT c.effective_url, left(p.text, 1500) AS preview
FROM public_v1.capture c
JOIN public_v1.prose p USING (content_id)
WHERE regexp_matches(lower(p.text),
  'acquisition criteria|seeking acquisitions|add-on acquisitions|buy-and-build');
