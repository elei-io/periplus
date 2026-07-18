CREATE MACRO macros.extract_json_ld(p_url, p_type, p_schema) AS TABLE (
    WITH pages AS MATERIALIZED (
        SELECT
            crawl_id,
            graph_run_id,
            captured_at,
            page_url,
            document_id
        FROM crawls
        WHERE (
                (
                    contains(p_url, '%')
                    AND (
                        normalized_url ILIKE p_url
                        OR page_url ILIKE p_url
                    )
                )
                OR (
                    NOT contains(p_url, '%')
                    AND (
                        normalized_url = p_url
                        OR page_url = p_url
                    )
                )
              )
          AND document_id IS NOT NULL
          AND outcome = 'success'
    ),
    parsed AS (
        SELECT
            page.crawl_id,
            page.graph_run_id,
            page.captured_at,
            page.page_url,
            node.document_id,
            node.element_index,
            node.script_ordinal,
            node.node_ordinal,
            node.json_path,
            node.context_json,
            node.node_id,
            node.node_types,
            from_json(node.node_json, p_schema) AS entity,
            node.node_json
        FROM pages AS page
        JOIN views.json_ld_nodes AS node USING (document_id)
        WHERE list_contains(node.node_types, p_type)
    )
    SELECT
        crawl_id,
        graph_run_id,
        captured_at,
        page_url,
        document_id,
        element_index,
        script_ordinal,
        node_ordinal,
        json_path,
        context_json,
        node_id,
        node_types,
        entity.*,
        node_json
    FROM parsed
);
