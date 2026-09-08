CREATE OR REPLACE VIEW public_v1.html_code AS
WITH code AS (
    SELECT c.content_id, c.node_index, c.subtree_end_index,
           count(p.node_index) > 0 AS block
    FROM public_v1.html_element c
    LEFT JOIN public_v1.html_element p ON p.content_id = c.content_id
     AND p.tag = 'pre' AND p.namespace = 'http://www.w3.org/1999/xhtml'
     AND c.node_index > p.node_index AND c.node_index < p.subtree_end_index
    WHERE c.tag = 'code' AND c.namespace = 'http://www.w3.org/1999/xhtml'
    GROUP BY c.content_id, c.node_index, c.subtree_end_index
)
SELECT c.content_id, c.node_index, c.block,
       coalesce(string_agg(n.value, '' ORDER BY n.node_index), '') AS text
FROM code c
LEFT JOIN public_v1.html_node n ON n.content_id = c.content_id
 AND n.node_index > c.node_index AND n.node_index < c.subtree_end_index
 AND n.node_type = 'text'
GROUP BY c.content_id, c.node_index, c.block;
