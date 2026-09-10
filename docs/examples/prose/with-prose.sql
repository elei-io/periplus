-- Same discovery and extraction, using materialized body text.
WITH source_content AS (
  SELECT DISTINCT content_id FROM public_v1.capture
  WHERE effective_url LIKE 'https://books.toscrape.com/catalogue/%/index.html'
    AND effective_url NOT LIKE 'https://books.toscrape.com/catalogue/category/%'
), candidates AS (
  SELECT content_id FROM public_v1.prose
  JOIN source_content USING (content_id)
  WHERE text ILIKE '%robot%'
)
SELECT DISTINCT c.effective_url AS url, h.text_direct AS title
FROM candidates m
JOIN public_v1.capture c USING (content_id)
JOIN public_v1.html_element h USING (content_id)
WHERE c.effective_url LIKE 'https://books.toscrape.com/catalogue/%/index.html'
  AND c.effective_url NOT LIKE 'https://books.toscrape.com/catalogue/category/%'
  AND h.tag = 'h1'
  AND h.namespace = 'http://www.w3.org/1999/xhtml'
ORDER BY url, title;
