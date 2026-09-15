SELECT p.url, c.capture_id, c.captured_at, s.document_id, s.score
FROM public_v1.search(['robot', 'science']) s
JOIN public_v1.capture c USING (document_id)
JOIN public_v1.page p ON p.url = c.url
ORDER BY s.score DESC, p.url, c.capture_id
LIMIT 100;
