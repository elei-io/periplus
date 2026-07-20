-- atlas:partition-by-day=captured_at
CREATE VIEW views.page_links AS
WITH successful_crawls AS (
    SELECT
        crawl_id,
        document_id,
        captured_at,
        page_url,
        url_registrable_domain AS source_registrable_domain,
        macros.normalize_url(page_url) AS source_url
    FROM crawls
    WHERE outcome = 'success'
      AND document_id IS NOT NULL
      AND (
          getvariable('atlas_materialization_crawl_id') IS NULL
          OR crawl_id = CAST(
              getvariable('atlas_materialization_crawl_id') AS UUID
          )
      )
),
scoped_elements AS MATERIALIZED (
    SELECT
        document_id,
        element_index,
        subtree_end_index,
        tag,
        attributes
    FROM elements
    WHERE getvariable('atlas_materialization_document_id') IS NULL
       OR document_id = getvariable('atlas_materialization_document_id')
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
        crawl.source_url,
        crawl.source_registrable_domain,
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
        macros.normalize_url(resolved_url) AS target_url
    FROM anchors
),
with_parts AS (
    SELECT
        *,
        macros.url_parts(source_url) AS source,
        macros.url_parts(target_url) AS target,
        macros.url_parts(resolved_url).fragment AS target_fragment
    FROM normalized
    WHERE target_url IS NOT NULL
),
classified AS (
    SELECT
        *,
        CASE
            WHEN target_url = source_url THEN 'same_url'
            WHEN target.scheme = source.scheme
             AND target.host = source.host
             AND target.port = source.port
             AND target.path = source.path THEN 'same_path'
            WHEN target.scheme = source.scheme
             AND target.host = source.host
             AND target.port = source.port THEN 'same_origin'
            WHEN target.host = source.host THEN 'same_host'
            WHEN target.host = source_registrable_domain
              OR ends_with(
                    target.host,
                    '.' || source_registrable_domain
                 ) THEN 'same_site'
            ELSE 'external'
        END AS relation_kind
    FROM with_parts
)
SELECT
    document_id,
    captured_at,
    source_url,
    source.scheme AS source_scheme,
    source.host AS source_host,
    source.port AS source_port,
    source_registrable_domain,
    source.path AS source_path,
    nullif(source.query, '') AS source_query,
    target_url,
    target.scheme AS target_scheme,
    target.host AS target_host,
    target.port AS target_port,
    target.path AS target_path,
    nullif(target.query, '') AS target_query,
    nullif(target_fragment, '') AS target_fragment,
    relation_kind,
    raw_href,
    element_index,
    crawl_id
FROM classified;
