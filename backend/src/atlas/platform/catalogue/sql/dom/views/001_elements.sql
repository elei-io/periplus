CREATE OR REPLACE VIEW dom.element AS
SELECT
    content_sha256 AS content_id,
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
FROM material.html_elements;
