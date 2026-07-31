CREATE OR REPLACE MACRO dom.query_selector(
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
FROM atlas_dom_select_first_keyed(
    (SELECT selected_content_id AS content_id),
    css_selector
);
