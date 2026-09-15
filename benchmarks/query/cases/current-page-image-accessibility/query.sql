WITH ranked_content AS (
    SELECT
        trim(
            regexp_extract(
                url,
                '^https?://(\[[^]]+\]|[^/:]+)',
                1
            ),
            '[]'
        ) AS hostname,
        document_id,
        row_number() OVER (
            PARTITION BY hostname
            ORDER BY hash(document_id), document_id
        ) AS host_rank
    FROM public_v1.capture
    WHERE document_id IS NOT NULL
),
scope AS MATERIALIZED (
    SELECT DISTINCT hostname, document_id
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
JOIN public_v1.html_element AS element USING (document_id)
WHERE element.tag = 'img'
GROUP BY scope.hostname
ORDER BY images DESC, scope.hostname;
