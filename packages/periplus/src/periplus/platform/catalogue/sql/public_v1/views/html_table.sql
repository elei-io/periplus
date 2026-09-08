CREATE OR REPLACE VIEW public_v1.html_table AS
WITH captions AS (
    SELECT * FROM public_v1.html_element
    WHERE tag = 'caption' AND namespace = 'http://www.w3.org/1999/xhtml'
    QUALIFY row_number() OVER (PARTITION BY content_id, parent_index ORDER BY node_index) = 1
)
SELECT t.content_id, t.node_index, c.node_index AS caption_node_index,
       CASE WHEN c.node_index IS NOT NULL THEN
           coalesce(string_agg(n.value, '' ORDER BY n.node_index)
               FILTER (WHERE nested.node_index IS NULL), '') END AS caption
FROM public_v1.html_element t
LEFT JOIN captions c ON c.content_id = t.content_id AND c.parent_index = t.node_index
LEFT JOIN public_v1.html_node n ON n.content_id = t.content_id
 AND n.node_index > c.node_index AND n.node_index < c.subtree_end_index AND n.node_type = 'text'
LEFT JOIN public_v1.html_element nested ON nested.content_id = t.content_id
 AND nested.tag = 'table' AND nested.namespace = 'http://www.w3.org/1999/xhtml'
 AND nested.node_index > c.node_index AND n.node_index > nested.node_index
 AND n.node_index < nested.subtree_end_index
WHERE t.tag = 'table' AND t.namespace = 'http://www.w3.org/1999/xhtml'
GROUP BY t.content_id, t.node_index, c.node_index;
