CREATE OR REPLACE VIEW public_v1.html_element AS
SELECT content_sha256 AS content_id, node_index, parent_index, subtree_end_index,
       sibling_index, depth, tag, namespace, attributes, text_direct, text
FROM material.html_elements;
