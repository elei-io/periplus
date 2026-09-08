CREATE OR REPLACE VIEW public_v1.html_heading AS
SELECT h.content_id, h.node_index, substr(h.tag, 2, 1)::INTEGER AS level,
       coalesce(string_agg(n.value, '' ORDER BY n.node_index), '') AS text
FROM public_v1.html_element h
LEFT JOIN public_v1.html_node n ON n.content_id = h.content_id
 AND n.node_index > h.node_index AND n.node_index < h.subtree_end_index
 AND n.node_type = 'text'
WHERE h.tag IN ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')
  AND h.namespace = 'http://www.w3.org/1999/xhtml'
GROUP BY h.content_id, h.node_index, h.tag;
