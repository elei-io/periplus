SELECT p.url, c.capture_id, c.captured_at, s.content_id, s.score
FROM public_v1.search(['robot', 'science']) s
JOIN public_v1.capture c USING (content_id)
JOIN public_v1.page p ON p.url = c.page_url
ORDER BY s.score DESC, p.url, c.capture_id
LIMIT 100;
