CREATE OR REPLACE VIEW public_v1.html_node AS
SELECT content_sha256 AS content_id, node_index, parent_index, subtree_end_index,
       sibling_index, depth, node_type, name, namespace, value,
       CASE WHEN node_type = 'text' THEN value ELSE NULL END AS text
FROM material.html_nodes;
