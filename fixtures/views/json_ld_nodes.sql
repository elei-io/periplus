CREATE VIEW views.json_ld_nodes AS
WITH scripts AS (
    SELECT
        document_id,
        element_index,
        row_number() OVER (
            PARTITION BY document_id
            ORDER BY element_index
        )::BIGINT AS script_ordinal,
        macros.text_content(document_id, element_index) AS json_text
    FROM elements
    WHERE tag = 'script'
      AND lower(
            trim(
                regexp_replace(
                    coalesce(macros.get_attribute(attributes, 'type'), ''),
                    '[ \t\r\n\f]+',
                    ' ',
                    'g'
                )
            )
          ) = 'application/ld+json'
),
roots AS (
    SELECT
        document_id,
        element_index,
        script_ordinal,
        cast(json_text AS JSON) AS root_json
    FROM scripts
    WHERE json_valid(json_text)
),
root_objects AS (
    SELECT
        document_id,
        element_index,
        script_ordinal,
        root_json,
        json_extract(root_json, '$."@context"') AS root_context,
        json_extract(root_json, '$."@graph"') AS root_graph
    FROM roots
    WHERE json_type(root_json) = 'OBJECT'
),
expanded AS (
    SELECT
        document_id,
        element_index,
        script_ordinal,
        1::BIGINT AS node_ordinal,
        '$' AS json_path,
        root_context,
        root_json AS node_json
    FROM root_objects
    WHERE json_type(root_graph) IS DISTINCT FROM 'ARRAY'

    UNION ALL

    SELECT
        root.document_id,
        root.element_index,
        root.script_ordinal,
        cast(item.key AS BIGINT) + 1 AS node_ordinal,
        '$[' || item.key || ']' AS json_path,
        NULL::JSON AS root_context,
        item.value AS node_json
    FROM roots AS root
    CROSS JOIN json_each(root.root_json) AS item
    WHERE json_type(root.root_json) = 'ARRAY'
      AND item.type = 'OBJECT'

    UNION ALL

    SELECT
        root.document_id,
        root.element_index,
        root.script_ordinal,
        cast(item.key AS BIGINT) + 1 AS node_ordinal,
        '$."@graph"[' || item.key || ']' AS json_path,
        root.root_context,
        item.value AS node_json
    FROM root_objects AS root
    CROSS JOIN json_each(root.root_graph) AS item
    WHERE json_type(root.root_graph) = 'ARRAY'
      AND item.type = 'OBJECT'
)
SELECT
    document_id,
    element_index,
    script_ordinal,
    node_ordinal,
    json_path,
    coalesce(
        json_extract(node_json, '$."@context"'),
        root_context
    ) AS context_json,
    CASE
        WHEN json_type(json_extract(node_json, '$."@id"')) = 'VARCHAR'
        THEN json_extract_string(node_json, '$."@id"')
    END AS node_id,
    coalesce(
        CASE json_type(json_extract(node_json, '$."@type"'))
            WHEN 'VARCHAR'
            THEN [json_extract_string(node_json, '$."@type"')]
            WHEN 'ARRAY'
            THEN (
                SELECT list(
                    json_extract_string(type_item.value, '$')
                    ORDER BY cast(type_item.key AS BIGINT)
                )
                FROM json_each(
                    json_extract(node_json, '$."@type"')
                ) AS type_item
                WHERE type_item.type = 'VARCHAR'
            )
        END,
        []::VARCHAR[]
    ) AS node_types,
    node_json
FROM expanded;
