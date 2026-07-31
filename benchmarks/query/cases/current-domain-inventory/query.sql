SELECT
    hostname,
    count(*) AS page_count,
    max(last_visited_at) AS most_recent_visit
FROM web.page
WHERE hostname IN (
    'docs.python.org',
    'developer.mozilla.org',
    'commoncrawl.org'
)
GROUP BY hostname
ORDER BY page_count DESC;
