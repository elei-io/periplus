CREATE OR REPLACE MACRO dom.query_selector_all(
    selected_content_id,
    css_selector
) AS TABLE
SELECT
    content_id,
    element_index,
    parent_index AS parent_element_index,
    subtree_end_index,
    depth,
    child_index AS sibling_index,
    tag AS tag_name,
    namespace,
    attributes,
    text_direct AS direct_text,
    text_tail AS tail_text
FROM atlas_dom_select_all(
    (
        SELECT
            element.content_id,
            element.element_index,
            element.parent_element_index AS parent_index,
            element.subtree_end_index,
            element.depth,
            element.sibling_index AS child_index,
            element.tag_name AS tag,
            element.namespace,
            element.attributes,
            element.direct_text AS text_direct,
            element.tail_text AS text_tail,
            false AS _atlas_document_end
        FROM dom.element AS element
        JOIN (SELECT selected_content_id AS content_id)
             AS selected USING (content_id)
        UNION ALL
        SELECT
            selected_content_id,
            NULL::INTEGER, NULL::INTEGER, NULL::INTEGER, NULL::INTEGER,
            NULL::INTEGER, NULL::VARCHAR, NULL::VARCHAR,
            NULL::MAP(VARCHAR, VARCHAR), NULL::VARCHAR, NULL::VARCHAR,
            true
        ORDER BY content_id, _atlas_document_end, element_index
    ),
    css_selector
);
