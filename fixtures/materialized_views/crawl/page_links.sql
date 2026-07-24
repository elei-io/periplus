-- atlas:partition-by-day=captured_at
-- atlas:refresh=keyed(crawl_id)
CREATE VIEW views.page_links AS
WITH successful_crawls AS MATERIALIZED (
    SELECT
        crawl.crawl_id,
        crawl.document_id,
        crawl.captured_at,
        effective.url_id AS source_url_id,
        effective.normalized_url AS page_url,
        effective.scheme AS source_scheme,
        effective.host AS source_host,
        effective.port AS source_port,
        effective.registrable_domain AS source_registrable_domain,
        effective.path AS source_path
    FROM crawls AS crawl
    JOIN urls AS requested ON requested.url_id = crawl.requested_url_id
    LEFT JOIN urls AS final ON final.url_id = crawl.final_url_id
    JOIN urls AS effective
      ON effective.url_id = coalesce(crawl.final_url_id, crawl.requested_url_id)
    WHERE crawl.outcome = 'success'
      AND crawl.document_id IS NOT NULL
),
scoped_elements AS MATERIALIZED (
    SELECT
        element.document_id,
        element.element_index,
        element.subtree_end_index,
        element.tag,
        element.attributes
    FROM elements AS element
    WHERE EXISTS (
        SELECT 1
        FROM successful_crawls AS crawl
        WHERE crawl.document_id = element.document_id
    )
),
base_candidates AS (
    SELECT
        crawl.crawl_id,
        base_element.element_index,
        macros.resolve_url(
            crawl.page_url,
            regexp_replace(
                macros.get_attribute(base_element.attributes, 'href'),
                '^[ \t\r\n\f]+|[ \t\r\n\f]+$',
                '',
                'g'
            )
        ) AS base_url
    FROM successful_crawls AS crawl
    JOIN scoped_elements AS base_element USING (document_id)
    JOIN scoped_elements AS head
      ON head.document_id = base_element.document_id
     AND head.tag = 'head'
     AND base_element.element_index > head.element_index
     AND base_element.element_index <= head.subtree_end_index
    WHERE base_element.tag = 'base'
      AND macros.has_attribute(base_element.attributes, 'href')
      AND regexp_replace(
            macros.get_attribute(base_element.attributes, 'href'),
            '^[ \t\r\n\f]+|[ \t\r\n\f]+$',
            '',
            'g'
          ) <> ''
),
document_bases AS (
    SELECT
        crawl_id,
        min_by(base_url, element_index) FILTER (
            WHERE macros.normalize_url(base_url) IS NOT NULL
        ) AS base_url
    FROM base_candidates
    GROUP BY crawl_id
),
anchors AS (
    SELECT
        crawl.crawl_id,
        crawl.document_id,
        crawl.captured_at,
        anchor.element_index,
        crawl.source_url_id,
        crawl.source_scheme,
        crawl.source_host,
        crawl.source_port,
        crawl.source_registrable_domain,
        crawl.source_path,
        regexp_replace(
            macros.get_attribute(anchor.attributes, 'href'),
            '^[ \t\r\n\f]+|[ \t\r\n\f]+$',
            '',
            'g'
        ) AS raw_href,
        macros.resolve_url(
            coalesce(document_bases.base_url, crawl.page_url),
            regexp_replace(
                macros.get_attribute(anchor.attributes, 'href'),
                '^[ \t\r\n\f]+|[ \t\r\n\f]+$',
                '',
                'g'
            )
        ) AS resolved_url
    FROM successful_crawls AS crawl
    JOIN scoped_elements AS anchor USING (document_id)
    LEFT JOIN document_bases USING (crawl_id)
    WHERE anchor.tag = 'a'
      AND macros.has_attribute(anchor.attributes, 'href')
      AND regexp_replace(
            macros.get_attribute(anchor.attributes, 'href'),
            '^[ \t\r\n\f]+|[ \t\r\n\f]+$',
            '',
            'g'
          ) <> ''
),
normalized AS (
    SELECT
        *,
        macros.normalize_url(resolved_url) AS target_url,
        macros.url_parts(resolved_url).fragment AS fragment
    FROM anchors
),
with_parts AS (
    SELECT
        *,
        macros.url_parts(target_url) AS target
    FROM normalized
    WHERE target_url IS NOT NULL
)
SELECT
    crawl_id,
    document_id,
    captured_at,
    element_index,
    source_url_id,
    sha256(target_url) AS target_url_id,
    raw_href,
    nullif(fragment, '') AS fragment,
    CASE
        WHEN target_url = (
            SELECT normalized_url FROM urls WHERE url_id = source_url_id LIMIT 1
        ) THEN 'same_url'
        WHEN target.scheme = source_scheme
         AND target.host = source_host
         AND target.port = source_port
         AND target.path = source_path THEN 'same_path'
        WHEN target.scheme = source_scheme
         AND target.host = source_host
         AND target.port = source_port THEN 'same_origin'
        WHEN target.host = source_host THEN 'same_host'
        WHEN target.host = source_registrable_domain
          OR ends_with(target.host, '.' || source_registrable_domain) THEN 'same_site'
        ELSE 'external'
    END AS relation_kind
FROM with_parts;
