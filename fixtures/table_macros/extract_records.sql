CREATE MACRO macros.extract_records(p_url, p_record_selector) AS TABLE (
    WITH pages AS MATERIALIZED (
        SELECT
            crawl_id,
            graph_run_id,
            captured_at,
            document_id,
            coalesce(final_url.normalized_url, requested_url.normalized_url) AS page_url
        FROM crawls AS crawl
        JOIN urls AS requested_url
          ON requested_url.url_id = crawl.requested_url_id
        LEFT JOIN urls AS final_url
          ON final_url.url_id = crawl.final_url_id
        WHERE (
                (
                    contains(p_url, '%')
                    AND (
                        requested_url.normalized_url ILIKE p_url
                        OR final_url.normalized_url ILIKE p_url
                    )
                )
                OR (
                    NOT contains(p_url, '%')
                    AND (
                        requested_url.normalized_url = p_url
                        OR final_url.normalized_url = p_url
                    )
                )
              )
          AND document_id IS NOT NULL
          AND outcome = 'success'
    ),
    element_base AS MATERIALIZED (
        SELECT
            page.crawl_id,
            page.page_url,
            element.document_id,
            element.element_index,
            element.parent_index,
            element.subtree_end_index,
            element.depth,
            element.tag,
            element.attributes,
            element.text_direct,
            element.text_tail,
            row_number() OVER (
                PARTITION BY
                    page.crawl_id,
                    element.document_id,
                    element.parent_index,
                    element.tag
                ORDER BY element.element_index
            )::BIGINT AS tag_ordinal
        FROM pages AS page
        JOIN elements AS element USING (document_id)
    ),
    sibling_totals AS MATERIALIZED (
        SELECT
            crawl_id,
            document_id,
            parent_index,
            tag,
            count(*)::BIGINT AS sibling_count
        FROM element_base
        GROUP BY crawl_id, document_id, parent_index, tag
    ),
    class_tokens AS MATERIALIZED (
        SELECT
            element.crawl_id,
            element.document_id,
            element.parent_index,
            element.element_index,
            element.tag,
            token,
            token_ordinal
        FROM element_base AS element
        CROSS JOIN UNNEST(
            regexp_split_to_array(
                trim(coalesce(macros.get_attribute(element.attributes, 'class'), '')),
                '[ \t\r\n\f]+'
            )
        ) WITH ORDINALITY AS tokens(token, token_ordinal)
        WHERE regexp_full_match(token, '[A-Za-z_-][A-Za-z0-9_-]*')
    ),
    class_token_totals AS MATERIALIZED (
        SELECT
            crawl_id,
            document_id,
            parent_index,
            tag,
            token,
            count(DISTINCT element_index)::BIGINT AS elements_with_token
        FROM class_tokens
        GROUP BY crawl_id, document_id, parent_index, tag, token
    ),
    selector_tokens AS MATERIALIZED (
        SELECT
            token.crawl_id,
            token.document_id,
            token.element_index,
            token.token
        FROM class_tokens AS token
        JOIN class_token_totals AS totals USING (
            crawl_id,
            document_id,
            parent_index,
            tag,
            token
        )
        JOIN sibling_totals AS siblings USING (
            crawl_id,
            document_id,
            parent_index,
            tag
        )
        WHERE siblings.sibling_count = 1
           OR (
                totals.elements_with_token >= 2
                AND totals.elements_with_token
                    / siblings.sibling_count::DOUBLE >= 0.25
           )
        QUALIFY row_number() OVER (
            PARTITION BY token.crawl_id, token.document_id, token.element_index
            ORDER BY token.token_ordinal, length(token.token), token.token
        ) = 1
    ),
    scoped AS MATERIALIZED (
        SELECT
            element.*,
            element.tag
                || coalesce('.' || selector.token, '') AS element_selector,
            element.tag
                || CASE
                    WHEN selector.token IS NOT NULL THEN '.' || selector.token
                    WHEN element.tag IN (
                        'ol', 'ul', 'dl', 'table', 'tbody', 'section'
                    ) AND siblings.sibling_count > 1
                        THEN ':nth-of-type(' || element.tag_ordinal || ')'
                    ELSE ''
                END AS container_selector
        FROM element_base AS element
        LEFT JOIN selector_tokens AS selector USING (
            crawl_id,
            document_id,
            element_index
        )
        JOIN sibling_totals AS siblings USING (
            crawl_id,
            document_id,
            parent_index,
            tag
        )
    ),
    matched AS MATERIALIZED (
        SELECT
            child.crawl_id,
            child.page_url,
            child.document_id,
            child.element_index,
            coalesce(paired.subtree_end_index, child.subtree_end_index)
                AS subtree_end_index,
            child.depth
        FROM scoped AS child
        JOIN scoped AS parent
          ON parent.crawl_id = child.crawl_id
         AND parent.document_id = child.document_id
         AND parent.element_index = child.parent_index
        LEFT JOIN scoped AS grandparent
          ON grandparent.crawl_id = parent.crawl_id
         AND grandparent.document_id = parent.document_id
         AND grandparent.element_index = parent.parent_index
        LEFT JOIN scoped AS paired
          ON paired.crawl_id = child.crawl_id
         AND paired.document_id = child.document_id
         AND paired.parent_index = child.parent_index
         AND paired.element_index = child.subtree_end_index + 1
         AND paired.element_selector != child.element_selector
        WHERE CASE
                WHEN strpos(parent.element_selector, '.') = 0
                     AND grandparent.container_selector IS NOT NULL
                    THEN grandparent.container_selector || ' > '
                ELSE ''
              END
                || parent.container_selector || ' > ' || child.element_selector
            = p_record_selector
    ),
    matched_totals AS MATERIALIZED (
        SELECT crawl_id, count(*)::BIGINT AS total_records
        FROM matched
        GROUP BY crawl_id
    ),
    records AS MATERIALIZED (
        SELECT
            *,
            row_number() OVER (
                PARTITION BY crawl_id
                ORDER BY element_index
            )::BIGINT AS record_number
        FROM matched
        QUALIFY row_number() OVER (
            PARTITION BY crawl_id
            ORDER BY element_index
        ) <= 2_000
    ),
    record_total AS (
        SELECT count(*)::BIGINT AS total_records FROM records
    ),
    field_instances AS MATERIALIZED (
        SELECT
            record.crawl_id,
            record.page_url,
            record.document_id,
            record.element_index AS record_element_index,
            field.element_index AS field_element_index,
            field.depth - record.depth AS relative_depth,
            CASE
                WHEN parent.element_index = record.element_index
                    THEN parent.element_selector || ' > ' || field.tag
                        || ':nth-of-type(' || field.tag_ordinal || ')'
                WHEN grandparent.element_index >= record.element_index
                    THEN grandparent.element_selector || ' > '
                        || parent.element_selector || ' > ' || field.tag
                        || ':nth-of-type(' || field.tag_ordinal || ')'
                ELSE parent.element_selector || ' > ' || field.tag
                    || ':nth-of-type(' || field.tag_ordinal || ')'
            END AS field_key,
            field.attributes,
            CASE
                WHEN field.tag NOT IN (
                    'script', 'style', 'template', 'noscript', 'svg', 'path'
                ) AND macros.has_text(field.text_direct)
                    THEN nullif(
                        trim(regexp_replace(
                            field.text_direct,
                            '[ \t\r\n\f]+',
                            ' ',
                            'g'
                        )),
                        ''
                    )
            END AS direct_text,
            CASE
                WHEN field.tag NOT IN (
                    'script', 'style', 'template', 'noscript', 'svg', 'path'
                ) AND macros.has_text(field.text_tail)
                    THEN nullif(
                        trim(regexp_replace(
                            field.text_tail,
                            '[ \t\r\n\f]+',
                            ' ',
                            'g'
                        )),
                        ''
                    )
            END AS tail_text
        FROM records AS record
        JOIN scoped AS field
          ON field.crawl_id = record.crawl_id
         AND field.document_id = record.document_id
         AND field.element_index > record.element_index
         AND field.element_index <= record.subtree_end_index
        JOIN scoped AS parent
          ON parent.crawl_id = field.crawl_id
         AND parent.document_id = field.document_id
         AND parent.element_index = field.parent_index
        LEFT JOIN scoped AS grandparent
          ON grandparent.crawl_id = parent.crawl_id
         AND grandparent.document_id = parent.document_id
         AND grandparent.element_index = parent.parent_index

        UNION ALL

        SELECT
            record.crawl_id,
            record.page_url,
            record.document_id,
            record.element_index AS record_element_index,
            root.element_index AS field_element_index,
            0::BIGINT AS relative_depth,
            ':scope' AS field_key,
            root.attributes,
            CASE
                WHEN root.tag NOT IN (
                    'script', 'style', 'template', 'noscript', 'svg', 'path'
                ) AND macros.has_text(root.text_direct)
                    THEN nullif(
                        trim(regexp_replace(
                            root.text_direct,
                            '[ \t\r\n\f]+',
                            ' ',
                            'g'
                        )),
                        ''
                    )
            END AS direct_text,
            CASE
                WHEN root.tag NOT IN (
                    'script', 'style', 'template', 'noscript', 'svg', 'path'
                ) AND macros.has_text(root.text_tail)
                    THEN nullif(
                        trim(regexp_replace(
                            root.text_tail,
                            '[ \t\r\n\f]+',
                            ' ',
                            'g'
                        )),
                        ''
                    )
            END AS tail_text
        FROM records AS record
        JOIN scoped AS root
          ON root.crawl_id = record.crawl_id
         AND root.document_id = record.document_id
         AND root.element_index = record.element_index
    ),
    raw_class_values AS MATERIALIZED (
        SELECT
            field.crawl_id,
            field.document_id,
            field.record_element_index,
            field.field_element_index,
            field.field_key,
            field.relative_depth,
            token
        FROM field_instances AS field
        CROSS JOIN UNNEST(
            regexp_split_to_array(
                trim(coalesce(macros.get_attribute(field.attributes, 'class'), '')),
                '[ \t\r\n\f]+'
            )
        ) AS tokens(token)
        WHERE regexp_full_match(
            token,
            '[A-Z]?[a-z]+(?:[-_][A-Za-z]+)*'
        )
    ),
    class_value_totals AS MATERIALIZED (
        SELECT
            field_key,
            token,
            count(DISTINCT (
                crawl_id,
                document_id,
                record_element_index
            ))::BIGINT AS records_with_token
        FROM raw_class_values
        GROUP BY field_key, token
    ),
    class_field_stats AS MATERIALIZED (
        SELECT
            field_key,
            count(DISTINCT token)::BIGINT AS distinct_tokens,
            count(DISTINCT (
                crawl_id,
                document_id,
                record_element_index
            ))::BIGINT AS records_with_class
        FROM raw_class_values
        GROUP BY field_key
    ),
    candidate_class_values AS MATERIALIZED (
        SELECT class.*
        FROM raw_class_values AS class
        JOIN class_value_totals AS totals USING (field_key, token)
        JOIN class_field_stats AS stats USING (field_key)
        CROSS JOIN record_total
        WHERE totals.records_with_token < record_total.total_records
          AND stats.distinct_tokens BETWEEN 2 AND 12
          AND stats.records_with_class
                / record_total.total_records::DOUBLE >= 0.5
    ),
    class_record_cardinality AS MATERIALIZED (
        SELECT
            field_key,
            crawl_id,
            document_id,
            record_element_index,
            count(DISTINCT token)::BIGINT AS token_count
        FROM candidate_class_values
        GROUP BY
            field_key,
            crawl_id,
            document_id,
            record_element_index
    ),
    class_cardinality_stats AS MATERIALIZED (
        SELECT
            field_key,
            mode(token_count)::BIGINT AS typical_tokens
        FROM class_record_cardinality
        GROUP BY field_key
    ),
    derived_class_values AS MATERIALIZED (
        SELECT candidate.*
        FROM candidate_class_values AS candidate
        JOIN class_cardinality_stats AS stats USING (field_key)
        WHERE stats.typical_tokens = 1
    ),
    source_instances AS MATERIALIZED (
        SELECT
            field.crawl_id,
            field.page_url,
            field.document_id,
            field.record_element_index,
            field.field_element_index,
            field.field_key,
            field.relative_depth,
            source.value_source,
            source.value
        FROM field_instances AS field
        CROSS JOIN LATERAL (
            SELECT 'direct_text' AS value_source, field.direct_text AS value
            UNION ALL SELECT 'tail_text', field.tail_text
            UNION ALL SELECT 'attribute:title', macros.get_attribute(field.attributes, 'title')
            UNION ALL SELECT 'attribute:alt', macros.get_attribute(field.attributes, 'alt')
            UNION ALL SELECT 'attribute:href', macros.get_attribute(field.attributes, 'href')
            UNION ALL SELECT 'attribute:src', macros.get_attribute(field.attributes, 'src')
            UNION ALL SELECT 'attribute:datetime', macros.get_attribute(field.attributes, 'datetime')
            UNION ALL SELECT 'attribute:content', macros.get_attribute(field.attributes, 'content')
            UNION ALL SELECT 'attribute:value', macros.get_attribute(field.attributes, 'value')
            UNION ALL SELECT 'attribute:itemprop', macros.get_attribute(field.attributes, 'itemprop')
            UNION ALL SELECT 'attribute:aria-label', macros.get_attribute(field.attributes, 'aria-label')
        ) AS source
        WHERE nullif(trim(source.value), '') IS NOT NULL

        UNION ALL

        SELECT
            class.crawl_id,
            field.page_url,
            class.document_id,
            class.record_element_index,
            class.field_element_index,
            class.field_key,
            class.relative_depth,
            'derived:class_token',
            class.token
        FROM derived_class_values AS class
        JOIN field_instances AS field
          ON field.crawl_id = class.crawl_id
         AND field.document_id = class.document_id
         AND field.record_element_index = class.record_element_index
         AND field.field_element_index = class.field_element_index
    ),
    per_record_source AS MATERIALIZED (
        SELECT
            crawl_id,
            document_id,
            record_element_index,
            field_key,
            value_source,
            min(relative_depth)::BIGINT AS nearest_depth,
            count(*)::BIGINT AS values_in_record
        FROM source_instances
        GROUP BY
            crawl_id,
            document_id,
            record_element_index,
            field_key,
            value_source
    ),
    source_totals AS MATERIALIZED (
        SELECT
            source.field_key,
            source.value_source,
            count(DISTINCT (
                source.crawl_id,
                source.document_id,
                source.record_element_index
            ))::BIGINT AS records_with_value,
            min(per_record.nearest_depth)::BIGINT AS nearest_depth,
            count(*)::BIGINT AS values,
            mode(per_record.values_in_record)::BIGINT AS typical_values,
            count(DISTINCT source.value)::BIGINT AS distinct_values,
            md5(to_json(list(DISTINCT source.value ORDER BY source.value)))
                AS value_signature,
            100.0 * count(*) FILTER (
                WHERE regexp_matches(
                    source.value,
                    '(^|[^A-Za-z])([$€£¥]|USD|EUR|GBP|JPY)([^A-Za-z]|$)'
                )
            ) / count(*) AS currency_pct
        FROM source_instances AS source
        JOIN per_record_source AS per_record
          ON per_record.crawl_id = source.crawl_id
         AND per_record.document_id = source.document_id
         AND per_record.record_element_index = source.record_element_index
         AND per_record.field_key = source.field_key
         AND per_record.value_source = source.value_source
        GROUP BY source.field_key, source.value_source
    ),
    field_scored AS MATERIALIZED (
        SELECT
            source.*,
            round(
                source.records_with_value / record_total.total_records
                * CASE
                    WHEN source.typical_values = 1 THEN 1.0
                    ELSE 1.0 / source.typical_values
                  END
                * (0.6 + 0.4 * least(
                    1.0,
                    ln(1 + source.distinct_values) / ln(11.0)
                  ))
                * CASE
                    WHEN source.value_source IN ('direct_text', 'tail_text') THEN 1.25
                    WHEN source.value_source IN ('attribute:title', 'attribute:alt') THEN 1.20
                    WHEN source.value_source IN ('attribute:href', 'attribute:src') THEN 1.15
                    WHEN source.value_source = 'derived:class_token' THEN 1.10
                    WHEN source.value_source = 'attribute:aria-label' THEN 0.75
                    ELSE 1.0
                  END
                * (1.0 + 0.8 * source.currency_pct / 100.0),
                3
            ) AS field_score
        FROM source_totals AS source
        CROSS JOIN record_total
    ),
    deduplicated AS MATERIALIZED (
        SELECT *
        FROM field_scored
        QUALIFY row_number() OVER (
            PARTITION BY value_signature
            ORDER BY
                field_score DESC,
                nearest_depth,
                field_key,
                value_source
        ) = 1
    ),
    selected AS MATERIALIZED (
        SELECT
            *,
            row_number() OVER (
                ORDER BY
                    field_score DESC,
                    records_with_value DESC,
                    nearest_depth,
                    field_key,
                    value_source
            )::BIGINT AS field_number,
            CASE
                WHEN value_source = 'direct_text' THEN 'text'
                WHEN value_source = 'tail_text' THEN 'tail_text'
                WHEN value_source = 'attribute:href' THEN 'resolved_href'
                WHEN value_source = 'attribute:src' THEN 'resolved_src'
                WHEN value_source = 'derived:class_token' THEN 'class_token'
                ELSE replace(value_source, 'attribute:', '')
            END AS value_kind
        FROM deduplicated
        QUALIFY field_number <= 12
    ),
    extracted_values AS MATERIALIZED (
        SELECT
            source.crawl_id,
            source.page_url,
            source.document_id,
            source.record_element_index,
            selected.field_number,
            selected.field_key,
            selected.value_kind,
            selected.value_source,
            list(
                CASE
                    WHEN source.value_source IN ('attribute:href', 'attribute:src')
                        THEN macros.resolve_url(source.page_url, source.value)
                    ELSE source.value
                END
                ORDER BY source.field_element_index
            ) AS values
        FROM source_instances AS source
        JOIN selected
          ON selected.field_key = source.field_key
         AND selected.value_source = source.value_source
        GROUP BY
            source.crawl_id,
            source.page_url,
            source.document_id,
            source.record_element_index,
            selected.field_number,
            selected.field_key,
            selected.value_kind,
            selected.value_source
    ),
    normalized_fields AS MATERIALIZED (
        SELECT
            record.crawl_id,
            record.page_url,
            record.document_id,
            record.element_index,
            record.record_number,
            selected.field_number,
            selected.field_key,
            selected.value_kind,
            selected.value_source,
            CASE
                WHEN len(extracted.values) = 1
                    THEN list_extract(extracted.values, 1)
                WHEN len(extracted.values) > 1
                    THEN to_json(extracted.values)::VARCHAR
                ELSE NULL
            END AS value
        FROM records AS record
        CROSS JOIN selected
        LEFT JOIN extracted_values AS extracted
          ON extracted.crawl_id = record.crawl_id
         AND extracted.document_id = record.document_id
         AND extracted.record_element_index = record.element_index
         AND extracted.field_number = selected.field_number
    ),
    rollup AS MATERIALIZED (
        SELECT
            crawl_id,
            page_url,
            document_id,
            element_index,
            record_number,
            count(*)::BIGINT AS field_count,
            max(value) FILTER (WHERE field_number = 1) AS field_1,
            max(value) FILTER (WHERE field_number = 2) AS field_2,
            max(value) FILTER (WHERE field_number = 3) AS field_3,
            max(value) FILTER (WHERE field_number = 4) AS field_4,
            max(value) FILTER (WHERE field_number = 5) AS field_5,
            max(value) FILTER (WHERE field_number = 6) AS field_6,
            max(value) FILTER (WHERE field_number = 7) AS field_7,
            max(value) FILTER (WHERE field_number = 8) AS field_8,
            max(value) FILTER (WHERE field_number = 9) AS field_9,
            max(value) FILTER (WHERE field_number = 10) AS field_10,
            max(value) FILTER (WHERE field_number = 11) AS field_11,
            max(value) FILTER (WHERE field_number = 12) AS field_12,
            list(
                field_key || ' :: ' || value_kind
                ORDER BY field_number
            ) AS field_definitions,
            to_json(map(
                list(
                    field_key || ' :: ' || value_kind
                    ORDER BY field_number
                ),
                list(value ORDER BY field_number)
            )) AS record_json
        FROM normalized_fields
        GROUP BY
            crawl_id,
            page_url,
            document_id,
            element_index,
            record_number
        HAVING count(value) > 0
    )
    SELECT
        page.crawl_id,
        page.graph_run_id,
        page.captured_at,
        page.page_url,
        rollup.record_number,
        p_record_selector AS record_selector,
        matched_totals.total_records AS matched_record_count,
        matched_totals.total_records > 2_000 AS records_truncated,
        rollup.field_count,
        rollup.field_1,
        rollup.field_2,
        rollup.field_3,
        rollup.field_4,
        rollup.field_5,
        rollup.field_6,
        rollup.field_7,
        rollup.field_8,
        rollup.field_9,
        rollup.field_10,
        rollup.field_11,
        rollup.field_12,
        rollup.field_definitions,
        rollup.record_json,
        macros.readable_text(rollup.document_id, rollup.element_index) AS record_text,
        macros.inner_html(rollup.document_id, rollup.element_index) AS record_html
    FROM rollup
    JOIN pages AS page USING (crawl_id)
    JOIN matched_totals USING (crawl_id)
    ORDER BY page.captured_at, page.crawl_id, rollup.record_number
);
