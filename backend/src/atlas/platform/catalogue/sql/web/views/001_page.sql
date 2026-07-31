CREATE OR REPLACE VIEW web.page AS
WITH ranked AS (
    SELECT
        coalesce(effective_url, requested_url) AS url,
        visit_id,
        finished_at,
        row_number() OVER (
            PARTITION BY coalesce(effective_url, requested_url)
            ORDER BY finished_at DESC NULLS LAST, visit_id DESC
        ) AS latest_rank
    FROM ingest.visits
),
pages AS (
    SELECT url, visit_id AS latest_page_visit_id,
           finished_at AS last_visited_at
    FROM ranked
    WHERE latest_rank = 1
)
SELECT
    url,
    split_part(url, '://', 1) AS scheme,
    trim(
        regexp_extract(
            url,
            '^https?://(\[[^]]+\]|[^/:]+)',
            1
        ),
        '[]'
    ) AS hostname,
    try_cast(
        nullif(
            regexp_extract(url, '^https?://[^/]+:(\d+)(?:/|$)', 1),
            ''
        )
        AS INTEGER
    ) AS port,
    coalesce(
        nullif(regexp_extract(url, '^https?://[^/]+(/[^?]*)', 1), ''),
        '/'
    ) AS path,
    nullif(regexp_extract(url, '\?(.*)$', 1), '') AS query_string,
    latest_page_visit_id,
    last_visited_at
FROM pages;
