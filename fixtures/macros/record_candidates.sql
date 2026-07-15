CREATE MACRO macros.record_candidates(p_url_pattern) AS TABLE (
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
            page.page_url,
            e.document_id,
            e.element_index,
            e.parent_index,
            e.subtree_end_index,
            e.tag,
            e.attributes,
            e.text_direct,
            CASE
                WHEN regexp_full_match(
                    coalesce(get_attribute(e.attributes, 'class'), ''),
                    '[A-Za-z_-][A-Za-z0-9_-]*([ \t\r\n\f]+[A-Za-z_-][A-Za-z0-9_-]*)*'
                )
                THEN e.tag || '.' || array_to_string(
                    list_sort(
                        regexp_split_to_array(
                            trim(get_attribute(e.attributes, 'class')),
                            '[ \t\r\n\f]+'
                        )
                    ),
                    '.'
                )
                ELSE e.tag
            END AS element_selector
        FROM latest_pages AS page
        JOIN elements AS e USING (document_id)
    ),
    sibling_size_counts AS MATERIALIZED (
        SELECT
            child.crawl_id,
            child.document_id,
            child.parent_index,
            child.element_selector AS record_selector,
            child.subtree_end_index - child.element_index AS descendant_elements,
            count(*)::BIGINT AS records_with_size
        FROM elements_in_scope AS child
        WHERE child.parent_index IS NOT NULL
        GROUP BY
            child.crawl_id,
            child.document_id,
            child.parent_index,
            child.element_selector,
            child.subtree_end_index - child.element_index
    ),
    sibling_modes AS MATERIALIZED (
        SELECT
            crawl_id,
            document_id,
            parent_index,
            record_selector,
            descendant_elements AS typical_descendant_elements
        FROM sibling_size_counts
        QUALIFY row_number() OVER (
            PARTITION BY crawl_id, document_id, parent_index, record_selector
            ORDER BY records_with_size DESC, descendant_elements
        ) = 1
    ),
    repeated_siblings AS MATERIALIZED (
        SELECT
            child.crawl_id,
            child.page_url,
            child.document_id,
            child.parent_index,
            parent.element_selector AS parent_selector,
            child.element_selector AS record_selector,
            parent.element_selector || ' > ' || child.element_selector AS record_key,
            count(*)::BIGINT AS records_in_container,
            sizes.typical_descendant_elements,
            count(*) FILTER (
                WHERE child.subtree_end_index - child.element_index
                      = sizes.typical_descendant_elements
            )::BIGINT AS structurally_consistent_records
        FROM elements_in_scope AS child
        JOIN elements_in_scope AS parent
          ON parent.crawl_id = child.crawl_id
         AND parent.document_id = child.document_id
         AND parent.element_index = child.parent_index
        JOIN sibling_modes AS sizes
          ON sizes.crawl_id = child.crawl_id
         AND sizes.document_id = child.document_id
         AND sizes.parent_index = child.parent_index
         AND sizes.record_selector = child.element_selector
        GROUP BY
            child.crawl_id,
            child.page_url,
            child.document_id,
            child.parent_index,
            parent.element_selector,
            child.element_selector,
            sizes.typical_descendant_elements
        HAVING count(*) >= 2
    ),
    representatives AS MATERIALIZED (
        SELECT
            groups.record_key,
            child.crawl_id,
            child.document_id,
            child.element_index,
            child.subtree_end_index
        FROM repeated_siblings AS groups
        JOIN elements_in_scope AS child
          ON child.crawl_id = groups.crawl_id
         AND child.document_id = groups.document_id
         AND child.parent_index = groups.parent_index
         AND child.element_selector = groups.record_selector
        QUALIFY row_number() OVER (
            PARTITION BY groups.record_key
            ORDER BY
                child.subtree_end_index - child.element_index
                    = groups.typical_descendant_elements DESC,
                child.page_url,
                child.document_id,
                child.element_index
        ) = 1
    ),
    previews AS MATERIALIZED (
        SELECT
            record_key,
            left(inner_html(document_id, element_index), 4000) AS example_html
        FROM representatives
    ),
    representative_fields AS MATERIALIZED (
        SELECT
            record.record_key,
            count(*)::BIGINT AS descendant_elements,
            count(DISTINCT field.element_selector)::BIGINT
                AS descendant_selectors,
            count(DISTINCT field.element_selector) FILTER (
                WHERE has_text(field.text_direct)
                   OR has_attribute(field.attributes, 'title')
                   OR has_attribute(field.attributes, 'href')
                   OR has_attribute(field.attributes, 'src')
                   OR has_attribute(field.attributes, 'itemprop')
            )::BIGINT AS value_field_count,
            count(*) FILTER (
                WHERE has_text(field.text_direct)
                   OR has_attribute(field.attributes, 'title')
                   OR has_attribute(field.attributes, 'href')
                   OR has_attribute(field.attributes, 'src')
                   OR has_attribute(field.attributes, 'itemprop')
            )::BIGINT AS value_bearing_descendants,
            bool_or(has_text(field.text_direct)) AS has_text_value,
            bool_or(has_attribute(field.attributes, 'href')) AS has_href_value,
            bool_or(has_attribute(field.attributes, 'src')) AS has_src_value,
            bool_or(has_attribute(field.attributes, 'title')) AS has_title_value,
            bool_or(has_attribute(field.attributes, 'itemprop')) AS has_itemprop_value
        FROM representatives AS record
        JOIN elements_in_scope AS field
          ON field.crawl_id = record.crawl_id
         AND field.document_id = record.document_id
         AND field.element_index > record.element_index
         AND field.element_index <= record.subtree_end_index
        GROUP BY record.record_key
    ),
    per_page AS (
        SELECT
            crawl_id,
            record_key,
            parent_selector,
            record_selector,
            count(*)::BIGINT AS containers,
            sum(records_in_container)::BIGINT AS records,
            mode(records_in_container)::BIGINT AS typical_records_per_container,
            min(records_in_container)::BIGINT AS min_records_per_container,
            max(records_in_container)::BIGINT AS max_records_per_container,
            mode(typical_descendant_elements)::BIGINT AS typical_descendant_elements,
            sum(structurally_consistent_records)::BIGINT AS structurally_consistent_records,
            min(page_url) AS example_page_url
        FROM repeated_siblings
        GROUP BY crawl_id, record_key, parent_selector, record_selector
    ),
    totals AS (
        SELECT
            record_key,
            parent_selector,
            record_selector,
            count(*)::BIGINT AS pages_with_records,
            sum(containers)::BIGINT AS containers,
            sum(records)::BIGINT AS records,
            mode(typical_records_per_container)::BIGINT
                AS typical_records_per_container,
            min(min_records_per_container)::BIGINT AS min_records_per_container,
            max(max_records_per_container)::BIGINT AS max_records_per_container,
            mode(typical_descendant_elements)::BIGINT AS typical_descendant_elements,
            sum(structurally_consistent_records)::BIGINT AS structurally_consistent_records,
            min(example_page_url) AS example_page_url
        FROM per_page
        GROUP BY record_key, parent_selector, record_selector
    ),
    scored AS (
        SELECT
            snapshot.graph_run_id,
            snapshot.captured_at,
            snapshot.captured_from,
            totals.record_key,
            totals.parent_selector,
            totals.record_selector,
            pages.total_pages,
            totals.pages_with_records,
            round(
                100.0 * totals.pages_with_records / nullif(pages.total_pages, 0),
                1
            ) AS page_coverage_pct,
            totals.containers,
            totals.records,
            totals.typical_records_per_container,
            totals.min_records_per_container,
            totals.max_records_per_container,
            totals.typical_descendant_elements,
            round(
                100.0
                * totals.structurally_consistent_records
                / nullif(totals.records, 0),
                1
            ) AS structural_consistency_pct,
            fields.descendant_elements,
            fields.descendant_selectors,
            fields.value_field_count,
            round(
                100.0
                * fields.value_bearing_descendants
                / nullif(fields.descendant_elements, 0),
                1
            ) AS value_bearing_descendant_pct,
            (
                fields.has_text_value::INTEGER
                + fields.has_href_value::INTEGER
                + fields.has_src_value::INTEGER
                + fields.has_title_value::INTEGER
                + fields.has_itemprop_value::INTEGER
            )::BIGINT AS value_type_count,
            round(
                100.0 * (
                    0.5 * least(
                        1.0,
                        ln(1 + fields.value_field_count) / ln(9.0)
                    )
                    + 0.3 * least(
                        1.0,
                        ln(1 + fields.descendant_selectors) / ln(17.0)
                    )
                    + 0.2
                        * fields.value_bearing_descendants
                        / nullif(fields.descendant_elements, 0)
                ),
                1
            ) AS information_density_score,
            round(
                sqrt(
                    totals.pages_with_records
                    / nullif(pages.total_pages, 0)
                    * totals.structurally_consistent_records
                    / nullif(totals.records, 0)
                )
                * ln(1 + totals.typical_records_per_container)
                * ln(1 + fields.value_field_count)
                * (
                    0.5
                    + fields.value_bearing_descendants
                      / nullif(fields.descendant_elements, 0)
                ),
                2
            ) AS record_score,
            previews.example_html,
            totals.example_page_url
        FROM totals
        JOIN previews USING (record_key)
        JOIN representative_fields AS fields USING (record_key)
        CROSS JOIN page_totals AS pages
        CROSS JOIN latest_snapshot AS snapshot
        WHERE fields.value_field_count > 0
    )
    SELECT *
    FROM scored
    ORDER BY
        record_score DESC,
        information_density_score DESC,
        page_coverage_pct DESC,
        structural_consistency_pct DESC,
        record_key
);
