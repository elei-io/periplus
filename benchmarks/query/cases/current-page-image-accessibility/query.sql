WITH ranked_content AS (
    SELECT
        trim(
            regexp_extract(
                coalesce(effective_url, requested_url),
                '^https?://(\[[^]]+\]|[^/:]+)',
                1
            ),
            '[]'
        ) AS hostname,
        content_id,
        row_number() OVER (
            PARTITION BY hostname
            ORDER BY hash(content_id), content_id
        ) AS host_rank
    FROM web.observation
    WHERE content_id IS NOT NULL
),
scope AS MATERIALIZED (
    SELECT DISTINCT hostname, content_id
    FROM ranked_content
    WHERE hostname IN (
        'docs.python.org',
        'developer.mozilla.org',
        'commoncrawl.org'
    )
      AND host_rank <= $scope
)
SELECT
    scope.hostname,
    count(*) AS images,
    count_if(NOT map_contains(element.attributes, 'alt')) AS missing_alt,
    count_if(map_extract_value(element.attributes, 'alt') = '') AS empty_alt
FROM scope
JOIN content.html_element AS element USING (content_id)
WHERE element.tag = 'img'
GROUP BY scope.hostname
ORDER BY images DESC, scope.hostname;
