CREATE OR REPLACE MACRO text_content_scoped(
    p_document_id,
    p_element_index,
    p_scope_document_id
) AS (
    WITH parameters AS (
        SELECT
            p_document_id AS requested_document_id,
            p_element_index AS requested_element_index
    ),
    root AS (
        SELECT
            parameters.requested_document_id,
            source.element_index,
            source.subtree_end_index
        FROM elements AS source, parameters
        WHERE source.document_id = parameters.requested_document_id
          AND source.element_index = parameters.requested_element_index
          AND (p_scope_document_id IS NULL
               OR source.document_id = p_scope_document_id)
    ),
    fragments AS (
        SELECT
            child.element_index AS event_index,
            0 AS event_phase,
            child.depth,
            child.text_direct AS fragment
        FROM elements AS child, root
        WHERE child.document_id = root.requested_document_id
          AND (p_scope_document_id IS NULL
               OR child.document_id = p_scope_document_id)
          AND child.element_index BETWEEN root.element_index AND root.subtree_end_index

        UNION ALL

        SELECT
            child.subtree_end_index AS event_index,
            1 AS event_phase,
            child.depth,
            child.text_tail AS fragment
        FROM elements AS child, root
        WHERE child.document_id = root.requested_document_id
          AND (p_scope_document_id IS NULL
               OR child.document_id = p_scope_document_id)
          AND child.element_index > root.element_index
          AND child.element_index <= root.subtree_end_index
    )
    SELECT coalesce(
        string_agg(
            fragment,
            '' ORDER BY event_index, event_phase, depth DESC
        ) FILTER (WHERE fragment <> ''),
        ''
    )
    FROM fragments
);
