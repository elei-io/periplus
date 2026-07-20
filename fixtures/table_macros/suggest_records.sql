CREATE MACRO macros.suggest_records(p_url) AS TABLE (
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
    history AS MATERIALIZED (
        SELECT
            count(DISTINCT crawl_id)::BIGINT AS matched_crawl_count,
            count(DISTINCT page_url)::BIGINT AS matched_page_count,
            min(captured_at) AS first_captured_at,
            max(captured_at) AS last_captured_at
        FROM pages
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
    sibling_sizes AS MATERIALIZED (
        SELECT
            child.crawl_id,
            child.document_id,
            child.parent_index,
            child.element_selector,
            mode(child.subtree_end_index - child.element_index)::BIGINT
                AS typical_descendants
        FROM scoped AS child
        WHERE child.parent_index IS NOT NULL
        GROUP BY
            child.crawl_id,
            child.document_id,
            child.parent_index,
            child.element_selector
        HAVING count(*) >= 2
    ),
    selector_elements AS MATERIALIZED (
        SELECT
            child.crawl_id,
            child.page_url,
            child.document_id,
            child.parent_index,
            child.element_index,
            child.element_selector,
            child.subtree_end_index AS own_subtree_end_index,
            coalesce(paired.subtree_end_index, child.subtree_end_index)
                AS subtree_end_index,
            child.depth,
            CASE
                WHEN strpos(parent.element_selector, '.') = 0
                     AND grandparent.container_selector IS NOT NULL
                    THEN grandparent.container_selector || ' > '
                ELSE ''
            END
                || parent.container_selector || ' > ' || child.element_selector
                AS record_key
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
        WHERE child.tag NOT IN (
            'script', 'style', 'meta', 'link', 'template', 'noscript',
            'svg', 'path', 'input', 'option', 'br'
        )
    ),
    record_elements AS MATERIALIZED (
        SELECT
            selector.*,
            sizes.typical_descendants,
            selector.own_subtree_end_index - selector.element_index
                = sizes.typical_descendants AS structurally_consistent
        FROM selector_elements AS selector
        JOIN sibling_sizes AS sizes USING (
            crawl_id,
            document_id,
            parent_index,
            element_selector
        )
    ),
    repeated AS MATERIALIZED (
        SELECT
            crawl_id,
            page_url,
            document_id,
            parent_index,
            record_key,
            count(*)::BIGINT AS records,
            typical_descendants,
            count(*) FILTER (
                WHERE structurally_consistent
            )::BIGINT AS consistent_records
        FROM record_elements
        GROUP BY
            crawl_id,
            page_url,
            document_id,
            parent_index,
            record_key,
            typical_descendants
    ),
    candidates AS MATERIALIZED (
        SELECT
            record_key,
            sum(records)::BIGINT AS records,
            sum(consistent_records)::BIGINT AS consistent_records,
            mode(typical_descendants)::BIGINT AS typical_descendants,
            min(page_url) AS example_page_url,
            round(
                20.0 * least(1.0, ln(1 + sum(records)) / ln(33.0))
                + 30.0 * sum(consistent_records) / sum(records)
                + 15.0 * least(
                    1.0,
                    ln(1 + mode(typical_descendants)) / ln(33.0)
                )
                - 5.0 * (
                    1.0 - sum(consistent_records) / sum(records)
                )
                - CASE
                    WHEN regexp_matches(
                        lower(record_key),
                        '(^|[^a-z0-9])(pager|pagination|subnav|sitemap|menu|nav|options|filter|filters|form|controls)([^a-z0-9]|$)'
                    ) THEN 35.0
                    ELSE 0.0
                  END
                - CASE
                    WHEN regexp_matches(
                        lower(record_key),
                        '> (div|section|ul|ol)$'
                    ) THEN 12.0
                    ELSE 0.0
                  END
                - CASE
                    WHEN sum(records) < 10
                     AND regexp_matches(
                        lower(record_key),
                        '> (div|section|ul|ol)$'
                     ) THEN 18.0
                    ELSE 0.0
                  END
                - CASE
                    WHEN mode(typical_descendants) <= 2
                     AND regexp_matches(
                        lower(record_key),
                        '> ((a|span|strong|option)([.][a-z0-9_-]+)?)$'
                     ) THEN 25.0
                    ELSE 0.0
                  END
                - CASE WHEN sum(records) < 3 THEN 15.0 ELSE 0.0 END,
                1
            ) AS preliminary_score
        FROM repeated
        GROUP BY record_key
        QUALIFY row_number() OVER (
            ORDER BY preliminary_score DESC, record_key
        ) <= 24
    ),
    record_instances_all AS MATERIALIZED (
        SELECT
            candidate.record_key,
            record.crawl_id,
            record.page_url,
            record.document_id,
            record.element_index,
            record.subtree_end_index,
            record.depth
        FROM candidates AS candidate
        JOIN selector_elements AS record USING (record_key)
    ),
    record_match_totals AS MATERIALIZED (
        SELECT record_key, count(*)::BIGINT AS total_records
        FROM record_instances_all
        GROUP BY record_key
    ),
    record_instances AS MATERIALIZED (
        SELECT *
        FROM record_instances_all
        QUALIFY row_number() OVER (
            PARTITION BY record_key
            ORDER BY crawl_id, document_id, element_index
        ) <= 512
    ),
    record_totals AS MATERIALIZED (
        SELECT record_key, count(*)::BIGINT AS total_records
        FROM record_instances
        GROUP BY record_key
    ),
    representatives AS MATERIALIZED (
        SELECT
            record_key,
            document_id,
            element_index
        FROM record_instances
        QUALIFY row_number() OVER (
            PARTITION BY record_key
            ORDER BY document_id, element_index
        ) = 1
    ),
    previews AS MATERIALIZED (
        SELECT
            record_key,
            left(macros.inner_html(document_id, element_index), 4000) AS example_html
        FROM representatives
    ),
    field_instances AS MATERIALIZED (
        SELECT
            record.record_key,
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
        FROM record_instances AS record
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
            record.record_key,
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
        FROM record_instances AS record
        JOIN scoped AS root
          ON root.crawl_id = record.crawl_id
         AND root.document_id = record.document_id
         AND root.element_index = record.element_index
    ),
    raw_class_values AS MATERIALIZED (
        SELECT
            field.record_key,
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
            class.record_key,
            class.field_key,
            class.token,
            count(DISTINCT (
                class.crawl_id,
                class.document_id,
                class.record_element_index
            ))::BIGINT AS records_with_token
        FROM raw_class_values AS class
        GROUP BY class.record_key, class.field_key, class.token
    ),
    class_field_stats AS MATERIALIZED (
        SELECT
            record_key,
            field_key,
            count(DISTINCT token)::BIGINT AS distinct_tokens,
            count(DISTINCT (
                crawl_id,
                document_id,
                record_element_index
            ))::BIGINT AS records_with_class
        FROM raw_class_values
        GROUP BY record_key, field_key
    ),
    candidate_class_values AS MATERIALIZED (
        SELECT class.*
        FROM raw_class_values AS class
        JOIN class_value_totals AS totals USING (record_key, field_key, token)
        JOIN class_field_stats AS stats USING (record_key, field_key)
        JOIN record_totals AS records USING (record_key)
        WHERE totals.records_with_token < records.total_records
          AND stats.distinct_tokens BETWEEN 2 AND 12
          AND stats.records_with_class
                / records.total_records::DOUBLE >= 0.5
    ),
    class_record_cardinality AS MATERIALIZED (
        SELECT
            record_key,
            field_key,
            crawl_id,
            document_id,
            record_element_index,
            count(DISTINCT token)::BIGINT AS token_count
        FROM candidate_class_values
        GROUP BY
            record_key,
            field_key,
            crawl_id,
            document_id,
            record_element_index
    ),
    class_cardinality_stats AS MATERIALIZED (
        SELECT
            record_key,
            field_key,
            mode(token_count)::BIGINT AS typical_tokens
        FROM class_record_cardinality
        GROUP BY record_key, field_key
    ),
    derived_class_values AS MATERIALIZED (
        SELECT candidate.*
        FROM candidate_class_values AS candidate
        JOIN class_cardinality_stats AS stats USING (record_key, field_key)
        WHERE stats.typical_tokens = 1
    ),
    source_instances AS MATERIALIZED (
        SELECT
            field.record_key,
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
            class.record_key,
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
          ON field.record_key = class.record_key
         AND field.crawl_id = class.crawl_id
         AND field.document_id = class.document_id
         AND field.record_element_index = class.record_element_index
         AND field.field_element_index = class.field_element_index
    ),
    per_record_source AS MATERIALIZED (
        SELECT
            record_key,
            crawl_id,
            document_id,
            record_element_index,
            field_key,
            value_source,
            min(relative_depth)::BIGINT AS nearest_depth,
            count(*)::BIGINT AS values_in_record
        FROM source_instances
        GROUP BY
            record_key,
            crawl_id,
            document_id,
            record_element_index,
            field_key,
            value_source
    ),
    source_totals AS MATERIALIZED (
        SELECT
            source.record_key,
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
            list_slice(
                list(DISTINCT source.value ORDER BY source.value),
                1,
                3
            ) AS examples,
            min(macros.resolve_url(source.page_url, source.value)) FILTER (
                WHERE source.value_source IN ('attribute:href', 'attribute:src')
            ) AS resolved_url,
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
          ON per_record.record_key = source.record_key
         AND per_record.crawl_id = source.crawl_id
         AND per_record.document_id = source.document_id
         AND per_record.record_element_index = source.record_element_index
         AND per_record.field_key = source.field_key
         AND per_record.value_source = source.value_source
        GROUP BY source.record_key, source.field_key, source.value_source
    ),
    field_scored AS MATERIALIZED (
        SELECT
            source.*,
            round(
                100.0 * source.records_with_value / records.total_records,
                1
            ) AS coverage_pct,
            round(
                100.0 * source.distinct_values / nullif(source.values, 0),
                1
            ) AS uniqueness_pct,
            round(
                source.records_with_value / records.total_records
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
        JOIN record_totals AS records USING (record_key)
    ),
    deduplicated AS MATERIALIZED (
        SELECT *
        FROM field_scored
        QUALIFY row_number() OVER (
            PARTITION BY record_key, value_signature
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
                PARTITION BY record_key
                ORDER BY
                    field_score DESC,
                    coverage_pct DESC,
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
    field_rollup AS MATERIALIZED (
        SELECT
            record_key,
            count(*)::BIGINT AS field_count,
            list(
                struct_pack(
                    field := 'field_' || field_number,
                    relative_selector := field_key,
                    value := value_kind,
                    source := value_source,
                    coverage_pct := coverage_pct,
                    uniqueness_pct := uniqueness_pct,
                    typical_values := typical_values,
                    score := field_score,
                    examples := examples,
                    resolved_url := resolved_url
                )
                ORDER BY field_number
            ) AS fields,
            avg(coverage_pct) AS average_field_coverage
        FROM selected
        GROUP BY record_key
    ),
    candidate_scored AS MATERIALIZED (
        SELECT
            candidate.*,
            matched.total_records AS matched_records,
            coalesce(fields.field_count, 0) AS field_count,
            fields.fields,
            round(
                candidate.preliminary_score
                + 20.0 * least(
                    1.0,
                    coalesce(fields.field_count, 0) / 8.0
                )
                + 20.0 * coalesce(fields.average_field_coverage, 0) / 100.0
                + 5.0 * coalesce(
                    least(
                        1.0,
                        coalesce(fields.field_count, 0)
                            / nullif(candidate.typical_descendants, 0)::DOUBLE
                    ),
                    0.0
                )
                - 15.0 * (
                    1.0 - coalesce(
                        least(
                            1.0,
                            coalesce(fields.field_count, 0)
                                / nullif(candidate.typical_descendants, 0)::DOUBLE
                        ),
                        0.0
                    )
                )
                - CASE
                    WHEN coalesce(fields.field_count, 0) <= 3 THEN 15.0
                    ELSE 0.0
                  END,
                1
            ) AS score
        FROM candidates AS candidate
        JOIN record_totals AS records USING (record_key)
        JOIN record_match_totals AS matched USING (record_key)
        LEFT JOIN field_rollup AS fields USING (record_key)
    )
    SELECT
        history.matched_crawl_count,
        history.matched_page_count,
        history.first_captured_at,
        history.last_captured_at,
        candidate.score,
        candidate.matched_records AS matched_record_count,
        candidate.record_key AS record_selector,
        candidate.field_count,
        candidate.fields,
        'SELECT * FROM macros.extract_records('''
            || replace(p_url, '''', '''''')
            || ''', '''
            || replace(candidate.record_key, '''', '''''')
            || ''');' AS extract_sql,
        round(
            100.0 * candidate.consistent_records / candidate.records,
            1
        ) AS structural_consistency_pct,
        round(
            100.0 * least(
                1.0,
                candidate.field_count / nullif(candidate.typical_descendants, 0)::DOUBLE
            ),
            1
        ) AS information_density_score,
        preview.example_html,
        candidate.example_page_url
    FROM candidate_scored AS candidate
    JOIN previews AS preview USING (record_key)
    CROSS JOIN history
    ORDER BY candidate.score DESC, candidate.record_key
    LIMIT 10
);
