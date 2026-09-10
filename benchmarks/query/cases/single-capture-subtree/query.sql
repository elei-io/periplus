WITH selected_capture AS (
 SELECT c.capture_id,c.requested_url,c.effective_url,c.captured_at,c.http_status_code,c.content_id
 FROM public_v1.capture AS c
 WHERE c.http_status_code = 200
 ORDER BY c.captured_at DESC NULLS LAST,c.capture_id DESC LIMIT 1
)
SELECT c.capture_id,c.requested_url,c.effective_url AS page_url,c.captured_at,c.http_status_code,c.content_id,
 l.node_index,e.parent_index,e.sibling_index,e.subtree_end_index,l.raw_href,l.resolved_url,e.attributes,e.text_direct,
 anchor_text.text AS text,anchor_text.truncated AS text_truncated,anchor_text.total_chars AS text_total_chars,
 anchor_text.node_count AS subtree_node_count
FROM selected_capture AS c
JOIN public_v1.link AS l ON l.capture_id=c.capture_id
LEFT JOIN public_v1.html_element AS e ON e.content_id=c.content_id AND e.node_index=l.node_index
LEFT JOIN LATERAL public_v1.subtree_text(c.content_id,l.node_index,max_chars := 100000,max_nodes := 10000) AS anchor_text ON TRUE
ORDER BY l.node_index;
