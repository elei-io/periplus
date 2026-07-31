SELECT
    target_hostname,
    count(*) AS directed_links,
    sum(occurrence_count) AS retained_occurrences,
    sum(page_visit_count) AS supporting_page_visits
FROM web.link
WHERE source_url LIKE 'https://docs.python.org/%'
  AND relationship = 'external'
GROUP BY target_hostname
ORDER BY retained_occurrences DESC
LIMIT 30;
