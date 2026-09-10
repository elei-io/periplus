SELECT regexp_extract(l.resolved_url, '^https?://([^/:]+)', 1) AS target_hostname,
 count(*) AS retained_occurrences, count(DISTINCT c.capture_id) AS supporting_captures
FROM public_v1.capture c JOIN public_v1.link l USING(capture_id)
WHERE c.effective_url LIKE 'https://docs.python.org/%'
 AND regexp_extract(l.resolved_url, '^https?://([^/:]+)', 1) <> 'docs.python.org'
GROUP BY target_hostname ORDER BY retained_occurrences DESC, target_hostname LIMIT 30;
