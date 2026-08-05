SELECT
    trim(
        regexp_extract(target_url, '^https?://(\[[^]]+\]|[^/:]+)', 1),
        '[]'
    ) AS target_hostname,
    count(*) AS retained_occurrences,
    count(DISTINCT observation_id) AS supporting_observations
FROM web.link_occurrence
WHERE source_url LIKE 'https://docs.python.org/%'
  AND relation_scope = 'external'
GROUP BY target_hostname
ORDER BY retained_occurrences DESC
LIMIT 30;
