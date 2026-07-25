-- atlas:description=Discovers JSON-LD entity types on captured pages matching a URL or URL pattern and proposes typed extraction schemas.
CREATE MACRO macros.suggest_json_ld_schemas(p_url) AS TABLE (
    WITH pages AS MATERIALIZED (
        SELECT
            crawl_id,
            content_captured_at AS captured_at,
            url AS page_url,
            document_id
        FROM crawls AS crawl
        WHERE (
                (
                    contains(p_url, '%')
                    AND (
                        requested_url ILIKE p_url
                        OR url ILIKE p_url
                    )
                )
                OR (
                    NOT contains(p_url, '%')
                    AND (
                        requested_url = p_url
                        OR url = p_url
                    )
                )
              )
          AND document_id IS NOT NULL
          AND outcome = 'success'
    ),
    typed AS MATERIALIZED (
        SELECT
            page.crawl_id,
            page.captured_at,
            page.page_url,
            node.document_id,
            node.script_ordinal,
            node.node_ordinal,
            type.entity_type,
            node.node_json
        FROM pages AS page
        JOIN views.json_ld_nodes AS node USING (document_id)
        CROSS JOIN UNNEST(
            list_distinct(node.node_types)
        ) AS type(entity_type)
        WHERE nullif(trim(type.entity_type), '') IS NOT NULL
    ),
    summary AS (
        SELECT
            entity_type,
            count(DISTINCT crawl_id)::BIGINT AS matched_crawl_count,
            count(DISTINCT document_id)::BIGINT AS matched_document_count,
            count(*)::BIGINT AS matched_node_count,
            min(captured_at) AS first_captured_at,
            max(captured_at) AS last_captured_at,
            cast(json_group_structure(node_json) AS VARCHAR) AS inferred_schema,
            first(
                node_json
                ORDER BY
                    captured_at,
                    page_url,
                    script_ordinal,
                    node_ordinal
            ) AS example_node
        FROM typed
        GROUP BY entity_type
    )
    SELECT
        entity_type,
        matched_crawl_count,
        matched_document_count,
        matched_node_count,
        first_captured_at,
        last_captured_at,
        inferred_schema,
        example_node,
        concat(
            'SELECT * FROM macros.extract_json_ld(',
            chr(10),
            '  ''',
            replace(p_url, '''', ''''''),
            ''',',
            chr(10),
            '  ''',
            replace(entity_type, '''', ''''''),
            ''',',
            chr(10),
            '  ''',
            replace(inferred_schema, '''', ''''''),
            '''',
            chr(10),
            ');'
        ) AS extract_sql
    FROM summary
    ORDER BY matched_node_count DESC, entity_type
);
