-- atlas:description=Reconstructs the inner HTML of one structural DOM element from retained DOM evidence.
CREATE OR REPLACE MACRO inner_html(p_document_id, p_element_index) AS (
    WITH parameters AS (
        SELECT
            p_document_id AS requested_document_id,
            p_element_index AS requested_element_index
    ),
    root AS (
        SELECT
            parameters.requested_document_id,
            source.element_index,
            source.subtree_end_index,
            source.tag
        FROM elements AS source, parameters
        WHERE source.document_id = parameters.requested_document_id
          AND source.element_index = parameters.requested_element_index
    ),
    events AS (
        SELECT
            child.element_index AS event_index,
            0 AS event_phase,
            child.depth,
            0 AS depth_phase,
            '<' || child.tag || coalesce((
                SELECT string_agg(
                    ' ' || entry.key || '="' ||
                    replace(replace(replace(replace(
                        entry.value,
                        '&', '&amp;'
                    ), '"', '&quot;'), '<', '&lt;'), '>', '&gt;') || '"',
                    '' ORDER BY entry.key
                )
                FROM unnest(map_entries(child.attributes)) AS attribute(entry)
            ), '') || '>' AS fragment
        FROM elements AS child, root
        WHERE child.document_id = root.requested_document_id
          AND child.element_index > root.element_index
          AND child.element_index <= root.subtree_end_index

        UNION ALL

        SELECT
            child.element_index AS event_index,
            1 AS event_phase,
            child.depth,
            0 AS depth_phase,
            CASE
                WHEN child.namespace_uri = 'http://www.w3.org/1999/xhtml'
                 AND child.tag IN ('script', 'style')
                    THEN child.text_direct
                ELSE replace(replace(replace(
                    child.text_direct,
                    '&', '&amp;'
                ), '<', '&lt;'), '>', '&gt;')
            END AS fragment
        FROM elements AS child, root
        WHERE child.document_id = root.requested_document_id
          AND child.element_index BETWEEN root.element_index AND root.subtree_end_index

        UNION ALL

        SELECT
            child.subtree_end_index AS event_index,
            2 AS event_phase,
            child.depth,
            0 AS depth_phase,
            '</' || child.tag || '>' AS fragment
        FROM elements AS child, root
        WHERE child.document_id = root.requested_document_id
          AND child.element_index > root.element_index
          AND child.element_index <= root.subtree_end_index
          AND NOT (
              child.namespace_uri = 'http://www.w3.org/1999/xhtml'
              AND child.tag IN (
                  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                  'link', 'meta', 'param', 'source', 'track', 'wbr'
              )
          )

        UNION ALL

        SELECT
            child.subtree_end_index AS event_index,
            2 AS event_phase,
            child.depth,
            1 AS depth_phase,
            replace(replace(replace(
                child.text_tail,
                '&', '&amp;'
            ), '<', '&lt;'), '>', '&gt;') AS fragment
        FROM elements AS child, root
        WHERE child.document_id = root.requested_document_id
          AND child.element_index > root.element_index
          AND child.element_index <= root.subtree_end_index
    )
    SELECT coalesce(
        string_agg(
            fragment,
            '' ORDER BY event_index, event_phase, depth DESC, depth_phase
        ) FILTER (WHERE fragment <> ''),
        ''
    )
    FROM events
);
