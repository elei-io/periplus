CREATE OR REPLACE MACRO dom.text_content(
    selected_content_id,
    selected_element_index
) AS TABLE
WITH selected_element AS (
    SELECT
        content_sha256,
        element_index,
        subtree_end_index
    FROM material.html_elements
    WHERE content_sha256 = selected_content_id
      AND element_index = selected_element_index
    LIMIT 1
)
SELECT
    selected_element.content_sha256 AS content_id,
    selected_element.element_index,
    (
        SELECT string_agg(
            fragment,
            ''
            ORDER BY event_index, event_kind, event_depth DESC
        )
        FROM (
            SELECT
                descendant.element_index AS event_index,
                1 AS event_kind,
                descendant.depth AS event_depth,
                descendant.text_direct AS fragment
            FROM material.html_elements AS descendant
            WHERE descendant.content_sha256 =
                  selected_element.content_sha256
              AND descendant.element_index >=
                  selected_element.element_index
              AND descendant.element_index <
                  selected_element.subtree_end_index

            UNION ALL

            SELECT
                descendant.subtree_end_index AS event_index,
                0 AS event_kind,
                descendant.depth AS event_depth,
                descendant.text_tail AS fragment
            FROM material.html_elements AS descendant
            WHERE descendant.content_sha256 =
                  selected_element.content_sha256
              AND descendant.element_index >
                  selected_element.element_index
              AND descendant.element_index <
                  selected_element.subtree_end_index
        ) AS text_event
    ) AS text_content
FROM selected_element;
