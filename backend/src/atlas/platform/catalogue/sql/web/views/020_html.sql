CREATE OR REPLACE VIEW web.html AS
SELECT
    element.content_sha256 AS content_id,
    element.element_index,
    element.parent_index,
    element.subtree_end_index,
    element.depth,
    element.child_index,
    element.tag,
    element.namespace,
    element.attributes,
    element.text_direct,
    element.text_tail,
    (
        SELECT string_agg(fragment, '' ORDER BY event_index, event_kind, event_depth DESC)
        FROM (
            SELECT
                descendant.element_index AS event_index,
                1 AS event_kind,
                descendant.depth AS event_depth,
                descendant.text_direct AS fragment
            FROM material.html_elements AS descendant
            WHERE descendant.content_sha256 = element.content_sha256
              AND descendant.element_index >= element.element_index
              AND descendant.element_index < element.subtree_end_index

            UNION ALL

            SELECT
                descendant.subtree_end_index AS event_index,
                0 AS event_kind,
                descendant.depth AS event_depth,
                descendant.text_tail AS fragment
            FROM material.html_elements AS descendant
            WHERE descendant.content_sha256 = element.content_sha256
              AND descendant.element_index > element.element_index
              AND descendant.element_index < element.subtree_end_index
        ) AS text_event
    ) AS text_content
FROM material.html_elements AS element;
