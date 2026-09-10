WITH pages AS (
 SELECT DISTINCT ON (requested_url)
   split_part(requested_url, '/', 3) AS domain,
   len(regexp_extract_all(text, '(?i)\b(AI|artificial intelligence)\b')) AS mentions
 FROM public_v1.capture JOIN public_v1.prose USING (content_id)
 ORDER BY requested_url, captured_at DESC, capture_id DESC
)
SELECT domain, sum(mentions) AS mentions, count(*) AS matching_pages
FROM pages WHERE mentions > 0
GROUP BY domain ORDER BY mentions DESC, domain LIMIT 20;
