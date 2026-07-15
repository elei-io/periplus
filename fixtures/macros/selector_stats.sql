CREATE MACRO macros.selector_stats(p_url_pattern) AS TABLE (
    WITH parameters AS (
        SELECT
            p_url_pattern AS url_pattern,
            regexp_extract(
                lower(p_url_pattern),
                '^[a-z][a-z0-9+.-]*://([^/:?#]+)',
                1
            ) AS host_pattern
    ),
    snapshot_candidates AS MATERIALIZED (
        SELECT
            graph_run_id,
            min(captured_at) AS captured_from,
            max(captured_at) AS captured_at
        FROM crawls, parameters
        WHERE page_url LIKE parameters.url_pattern
          AND (
              parameters.host_pattern = ''
              OR url_host LIKE parameters.host_pattern
          )
          AND document_id IS NOT NULL
          AND outcome = 'success'
        GROUP BY graph_run_id
    ),
    latest_snapshot AS MATERIALIZED (
        SELECT graph_run_id, captured_from, captured_at
        FROM snapshot_candidates
        ORDER BY captured_at DESC, graph_run_id DESC
        LIMIT 1
    ),
    latest_pages AS MATERIALIZED (
        SELECT crawl_id, document_id, page_url
        FROM crawls
        JOIN latest_snapshot USING (graph_run_id)
        CROSS JOIN parameters
        WHERE page_url LIKE parameters.url_pattern
          AND (
              parameters.host_pattern = ''
              OR url_host LIKE parameters.host_pattern
          )
          AND document_id IS NOT NULL
          AND outcome = 'success'
        QUALIFY row_number() OVER (
            PARTITION BY page_url
            ORDER BY crawls.captured_at DESC, crawl_id DESC
        ) = 1
    ),
    page_totals AS (
        SELECT count(*)::BIGINT AS total_pages
        FROM latest_pages
    ),
    elements_in_scope AS MATERIALIZED (
        SELECT
            page.crawl_id,
            e.tag,
            e.attributes,
            e.text_direct,
            get_attribute(e.attributes, 'class') AS class_value
        FROM latest_pages AS page
        JOIN elements AS e USING (document_id)
    ),
    candidates AS (
        SELECT
            e.crawl_id,
            candidate.selector,
            has_text(e.text_direct) AS has_non_empty_text,
            CASE
                WHEN has_text(e.text_direct)
                THEN nullif(
                    trim(
                        regexp_replace(
                            e.text_direct,
                            '[ \t\r\n\f]+',
                            ' ',
                            'g'
                        )
                    ),
                    ''
                )
            END AS example_text,
            get_attribute(e.attributes, 'title') AS example_title,
            get_attribute(e.attributes, 'href') AS example_href
        FROM elements_in_scope AS e
        CROSS JOIN UNNEST([
            e.tag,
            CASE
                WHEN regexp_full_match(
                    coalesce(e.class_value, ''),
                    '[A-Za-z_-][A-Za-z0-9_-]*([ \t\r\n\f]+[A-Za-z_-][A-Za-z0-9_-]*)*'
                )
                THEN e.tag || '.' || array_to_string(
                    list_sort(
                        regexp_split_to_array(
                            trim(e.class_value),
                            '[ \t\r\n\f]+'
                        )
                    ),
                    '.'
                )
            END,
            CASE WHEN has_attribute(e.attributes, 'title') THEN e.tag || '[title]' END,
            CASE WHEN has_attribute(e.attributes, 'href') THEN e.tag || '[href]' END,
            CASE WHEN has_attribute(e.attributes, 'src') THEN e.tag || '[src]' END,
            CASE WHEN has_attribute(e.attributes, 'itemprop') THEN e.tag || '[itemprop]' END
        ]) AS candidate(selector)
        WHERE candidate.selector IS NOT NULL
    ),
    per_page AS (
        SELECT
            selector,
            crawl_id,
            count(*)::BIGINT AS matches_per_page,
            count(*) FILTER (WHERE has_non_empty_text)::BIGINT AS non_empty_text_matches,
            min(example_text) FILTER (WHERE example_text IS NOT NULL) AS example_text,
            min(example_title) FILTER (WHERE example_title IS NOT NULL) AS example_title,
            min(example_href) FILTER (WHERE example_href IS NOT NULL) AS example_href
        FROM candidates
        GROUP BY selector, crawl_id
    ),
    selector_totals AS (
        SELECT
            selector,
            count(*)::BIGINT AS pages_with_matches,
            sum(matches_per_page)::BIGINT AS occurrences,
            mode(matches_per_page)::BIGINT AS typical_matches_per_page,
            min(matches_per_page)::BIGINT AS minimum_on_matching_pages,
            max(matches_per_page)::BIGINT AS max_matches_per_page,
            count(*) FILTER (WHERE matches_per_page = 1)::BIGINT AS pages_with_one_match,
            sum(non_empty_text_matches)::BIGINT AS non_empty_text_matches,
            min(example_text) FILTER (WHERE example_text IS NOT NULL) AS example_text,
            min(example_title) FILTER (WHERE example_title IS NOT NULL) AS example_title,
            min(example_href) FILTER (WHERE example_href IS NOT NULL) AS example_href
        FROM per_page
        GROUP BY selector
    )
    SELECT
        snapshot.graph_run_id,
        snapshot.captured_at,
        snapshot.captured_from,
        totals.selector,
        pages.total_pages,
        totals.pages_with_matches,
        round(
            100.0 * totals.pages_with_matches / nullif(pages.total_pages, 0),
            1
        ) AS page_coverage_pct,
        totals.occurrences,
        totals.typical_matches_per_page,
        CASE
            WHEN totals.pages_with_matches < pages.total_pages THEN 0
            ELSE totals.minimum_on_matching_pages
        END AS min_matches_per_page,
        totals.max_matches_per_page,
        round(
            100.0 * totals.pages_with_one_match / nullif(pages.total_pages, 0),
            1
        ) AS pages_with_one_match_pct,
        round(
            100.0 * totals.non_empty_text_matches / nullif(totals.occurrences, 0),
            1
        ) AS non_empty_text_pct,
        totals.example_text,
        totals.example_title,
        totals.example_href
    FROM selector_totals AS totals
    CROSS JOIN page_totals AS pages
    CROSS JOIN latest_snapshot AS snapshot
    ORDER BY
        page_coverage_pct DESC,
        typical_matches_per_page DESC,
        totals.selector
);
