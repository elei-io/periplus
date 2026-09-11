SELECT content_id
FROM public_v1.prose
WHERE regexp_matches(text, '\bmonkeys\b')
  AND regexp_matches(text, '\bzoo\b')
ORDER BY content_id
