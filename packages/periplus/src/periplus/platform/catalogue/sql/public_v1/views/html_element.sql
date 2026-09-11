CREATE OR REPLACE VIEW public_v1.html_element AS
SELECT e.content_sha256 AS content_id, e.node_index, e.parent_index,
       e.subtree_end_index, e.sibling_index, e.depth, e.tag,
       e.namespace, e.attributes, e.text_direct,
       (SELECT coalesce(string_agg(n.value, '' ORDER BY n.node_index), '')
        FROM material.html_nodes n
        WHERE n.content_sha256 = e.content_sha256 AND n.node_type = 'text'
          AND n.node_index > e.node_index AND n.node_index < e.subtree_end_index) AS text
FROM material.html_nodes e
WHERE e.node_type = 'element';
