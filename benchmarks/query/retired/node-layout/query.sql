SELECT e.tag, count(*) AS matching_nodes
FROM public_v1.term_node t
JOIN public_v1.html_node n USING (content_id, node_index)
JOIN public_v1.html_element e ON e.content_id=n.content_id AND e.node_index=n.parent_index
WHERE t.text='monkey' AND n.node_type='text'
GROUP BY e.tag ORDER BY e.tag;
