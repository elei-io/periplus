CREATE OR REPLACE VIEW dom.elements AS
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
    element.text_tail
FROM material.html_elements AS element;
