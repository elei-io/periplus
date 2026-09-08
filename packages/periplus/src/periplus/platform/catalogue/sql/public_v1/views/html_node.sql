CREATE OR REPLACE VIEW public_v1.html_node AS
SELECT content_sha256 AS content_id, node_index, parent_index, subtree_end_index,
       sibling_index, depth, node_type, name, namespace, value
FROM material.html_nodes;
