SELECT
    trim(
        regexp_extract(
            coalesce(effective_url, requested_url),
            '^https?://(\[[^]]+\]|[^/:]+)',
            1
        ),
        '[]'
    ) AS hostname,
    count(*) AS observation_count,
    max(observed_at) AS most_recent_observation
FROM web.observation
WHERE hostname IN (
    'docs.python.org',
    'developer.mozilla.org',
    'commoncrawl.org'
)
GROUP BY hostname
ORDER BY observation_count DESC;
