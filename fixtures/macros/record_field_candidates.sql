CREATE MACRO macros.record_field_candidates(p_url_pattern, p_record_key) AS TABLE (
    WITH parameters AS (
        SELECT
            p_url_pattern AS url_pattern,
            p_record_key AS record_key,
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
    elements_in_scope AS MATERIALIZED (
        SELECT
            page.crawl_id,
            page.page_url,
            e.document_id,
            e.element_index,
            e.parent_index,
            e.subtree_end_index,
            e.depth,
            e.tag,
            e.attributes,
            e.text_direct,
            e.text_tail,
            row_number() OVER (
                PARTITION BY page.crawl_id, e.document_id, e.parent_index, e.tag
                ORDER BY e.element_index
            )::BIGINT AS tag_ordinal,
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
    record_instances AS MATERIALIZED (
        SELECT
            child.crawl_id,
            child.page_url,
            child.document_id,
            child.element_index,
            child.subtree_end_index,
            child.depth
        FROM elements_in_scope AS child
        JOIN elements_in_scope AS parent
          ON parent.crawl_id = child.crawl_id
         AND parent.document_id = child.document_id
         AND parent.element_index = child.parent_index
        CROSS JOIN parameters
        WHERE parent.element_selector || ' > ' || child.element_selector
              = parameters.record_key
        QUALIFY count(*) OVER (
            PARTITION BY
                child.crawl_id,
                child.document_id,
                child.parent_index,
                child.element_selector
        ) >= 2
    ),
    record_totals AS (
        SELECT
            count(*)::BIGINT AS total_records,
            count(DISTINCT crawl_id)::BIGINT AS pages_with_records
        FROM record_instances
    ),
    field_instances AS MATERIALIZED (
        SELECT
            record.crawl_id,
            record.page_url,
            record.document_id,
            record.element_index AS record_element_index,
            field.element_index AS field_element_index,
            parent.element_selector || ' > ' || field.tag
                || ':nth-of-type(' || field.tag_ordinal || ')' AS field_key,
            field.element_selector AS field_selector,
            field.depth - record.depth AS relative_depth,
            field.attributes,
            CASE
                WHEN has_text(field.text_direct)
                THEN nullif(
                    trim(regexp_replace(field.text_direct, '[ \t\r\n\f]+', ' ', 'g')),
                    ''
                )
            END AS direct_text,
            CASE
                WHEN has_text(field.text_tail)
                THEN nullif(
                    trim(regexp_replace(field.text_tail, '[ \t\r\n\f]+', ' ', 'g')),
                    ''
                )
            END AS tail_text
        FROM record_instances AS record
        JOIN elements_in_scope AS field
          ON field.crawl_id = record.crawl_id
         AND field.document_id = record.document_id
         AND field.element_index > record.element_index
         AND field.element_index <= record.subtree_end_index
        JOIN elements_in_scope AS parent
          ON parent.crawl_id = field.crawl_id
         AND parent.document_id = field.document_id
         AND parent.element_index = field.parent_index
    ),
    source_instances AS MATERIALIZED (
        SELECT
            crawl_id,
            page_url,
            document_id,
            record_element_index,
            field_element_index,
            field_key,
            field_selector,
            relative_depth,
            source.value_source,
            source.value
        FROM field_instances AS field
        CROSS JOIN LATERAL (
            SELECT 'direct_text' AS value_source, field.direct_text AS value
            UNION ALL
            SELECT 'tail_text', field.tail_text
            UNION ALL
            SELECT 'attribute:' || entry.key, entry.value
            FROM UNNEST(map_entries(field.attributes)) AS attribute_entries(entry)
        ) AS source
        WHERE nullif(trim(source.value), '') IS NOT NULL
    ),
    field_representatives AS MATERIALIZED (
        SELECT
            field_key,
            document_id,
            field_element_index
        FROM source_instances
        QUALIFY row_number() OVER (
            PARTITION BY field_key
            ORDER BY document_id, field_element_index, value_source, value
        ) = 1
    ),
    field_previews AS MATERIALIZED (
        SELECT
            field_key,
            left(inner_html(document_id, field_element_index), 4000) AS example_html
        FROM field_representatives
    ),
    per_record_source AS (
        SELECT
            crawl_id,
            document_id,
            record_element_index,
            field_key,
            value_source,
            min(relative_depth)::BIGINT AS nearest_relative_depth,
            count(*)::BIGINT AS values_in_record
        FROM source_instances
        GROUP BY
            crawl_id,
            document_id,
            record_element_index,
            field_key,
            value_source
    ),
    source_totals AS (
        SELECT
            source_values.field_key,
            mode(source_values.field_selector) AS field_selector,
            list(DISTINCT source_values.field_selector ORDER BY source_values.field_selector)
                AS selector_variants,
            source_values.value_source,
            count(DISTINCT (
                source_values.crawl_id,
                source_values.document_id,
                source_values.record_element_index
            ))::BIGINT AS records_with_value,
            min(records.nearest_relative_depth)::BIGINT AS nearest_relative_depth,
            count(*)::BIGINT AS values,
            mode(records.values_in_record)::BIGINT AS typical_values_per_record,
            min(records.values_in_record)::BIGINT AS min_values_per_matching_record,
            max(records.values_in_record)::BIGINT AS max_values_per_record,
            count(DISTINCT source_values.value)::BIGINT AS distinct_values,
            list_slice(
                list(DISTINCT source_values.value ORDER BY source_values.value),
                1,
                3
            )
                AS example_values,
            min(
                resolve_url(source_values.page_url, source_values.value)
            ) FILTER (
                WHERE source_values.value_source IN ('attribute:href', 'attribute:src')
            ) AS example_resolved_url
        FROM source_instances AS source_values
        JOIN per_record_source AS records
          ON records.crawl_id = source_values.crawl_id
         AND records.document_id = source_values.document_id
         AND records.record_element_index = source_values.record_element_index
         AND records.field_key = source_values.field_key
         AND records.value_source = source_values.value_source
        GROUP BY
            source_values.field_key,
            source_values.value_source
    ),
    scored AS (
        SELECT
            snapshot.graph_run_id,
            snapshot.captured_at,
            snapshot.captured_from,
            parameters.record_key,
            record_totals.total_records,
            record_totals.pages_with_records,
            fields.field_key,
            fields.field_selector,
            fields.selector_variants,
            fields.value_source,
            fields.nearest_relative_depth,
            fields.records_with_value,
            round(
                100.0 * fields.records_with_value
                / nullif(record_totals.total_records, 0),
                1
            ) AS record_coverage_pct,
            fields.values,
            fields.typical_values_per_record,
            CASE
                WHEN fields.records_with_value < record_totals.total_records THEN 0
                ELSE fields.min_values_per_matching_record
            END AS min_values_per_record,
            fields.max_values_per_record,
            fields.distinct_values,
            round(
                100.0 * fields.distinct_values / nullif(fields.values, 0),
                1
            ) AS uniqueness_pct,
            fields.distinct_values = 1 AS is_constant,
            CASE
                WHEN fields.value_source IN ('attribute:title', 'attribute:alt') THEN 5
                WHEN fields.value_source IN ('attribute:href', 'attribute:src') THEN 4
                WHEN fields.value_source IN (
                    'direct_text', 'tail_text', 'attribute:datetime',
                    'attribute:content', 'attribute:value', 'attribute:itemprop'
                ) THEN 3
                WHEN fields.value_source = 'attribute:class' THEN 2
                ELSE 1
            END AS source_priority,
            round(
                fields.records_with_value / nullif(record_totals.total_records, 0)
                * CASE
                    WHEN fields.typical_values_per_record = 1 THEN 1.0
                    ELSE 1.0 / fields.typical_values_per_record
                  END
                * (0.5 + 0.5 * least(
                    1.0,
                    ln(1 + fields.distinct_values) / ln(11.0)
                  ))
                * (1.0 + 0.1 * CASE
                    WHEN fields.value_source IN ('attribute:title', 'attribute:alt') THEN 5
                    WHEN fields.value_source IN ('attribute:href', 'attribute:src') THEN 4
                    WHEN fields.value_source IN (
                        'direct_text', 'tail_text', 'attribute:datetime',
                        'attribute:content', 'attribute:value', 'attribute:itemprop'
                    ) THEN 3
                    WHEN fields.value_source = 'attribute:class' THEN 2
                    ELSE 1
                  END),
                3
            ) AS field_score,
            fields.example_values,
            fields.example_resolved_url,
            previews.example_html
        FROM source_totals AS fields
        JOIN field_previews AS previews USING (field_key)
        CROSS JOIN record_totals
        CROSS JOIN latest_snapshot AS snapshot
        CROSS JOIN parameters
    )
    SELECT *
    FROM scored
    ORDER BY
        field_score DESC,
        record_coverage_pct DESC,
        typical_values_per_record = 1 DESC,
        uniqueness_pct DESC,
        source_priority DESC,
        nearest_relative_depth,
        field_key,
        value_source
);
