CREATE OR REPLACE VIEW content.html_element AS
SELECT
    content_sha256 AS content_id,
    element_index,
    parent_index,
    subtree_end_index,
    depth,
    child_index,
    tag,
    namespace,
    attributes,
    text_direct,
    text_tail
FROM material.html_elements AS element
WHERE EXISTS (
    SELECT 1 FROM content.object AS object
    WHERE object.content_id = element.content_sha256
);
