CREATE OR REPLACE VIEW public_v1.html_section AS
WITH headings AS (
    SELECT content_id, node_index, subtree_end_index, substr(tag, 2, 1)::INTEGER AS level
    FROM public_v1.html_element
    WHERE tag IN ('h1','h2','h3','h4','h5','h6')
      AND namespace = 'http://www.w3.org/1999/xhtml'
), boundaries AS (
    SELECT content_id, node_index AS heading_node_index,
           CASE level
           WHEN 2 THEN max(CASE WHEN level < 2 THEN node_index END) OVER preceding
           WHEN 3 THEN max(CASE WHEN level < 3 THEN node_index END) OVER preceding
           WHEN 4 THEN max(CASE WHEN level < 4 THEN node_index END) OVER preceding
           WHEN 5 THEN max(CASE WHEN level < 5 THEN node_index END) OVER preceding
           WHEN 6 THEN max(CASE WHEN level < 6 THEN node_index END) OVER preceding
           END::INTEGER AS parent_heading_node_index,
           subtree_end_index AS start_node_index,
           CASE level
           WHEN 1 THEN min(CASE WHEN level <= 1 THEN node_index END) OVER following
           WHEN 2 THEN min(CASE WHEN level <= 2 THEN node_index END) OVER following
           WHEN 3 THEN min(CASE WHEN level <= 3 THEN node_index END) OVER following
           WHEN 4 THEN min(CASE WHEN level <= 4 THEN node_index END) OVER following
           WHEN 5 THEN min(CASE WHEN level <= 5 THEN node_index END) OVER following
           WHEN 6 THEN min(CASE WHEN level <= 6 THEN node_index END) OVER following
           END::INTEGER AS next_heading_node_index
    FROM headings
    WINDOW preceding AS (PARTITION BY content_id ORDER BY node_index
                         ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
           following AS (PARTITION BY content_id ORDER BY node_index
                         ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING)
)
SELECT b.content_id, b.heading_node_index, b.parent_heading_node_index,
       b.start_node_index, greatest(b.start_node_index, coalesce(b.next_heading_node_index, root.subtree_end_index)) AS end_node_index
FROM boundaries b
JOIN public_v1.html_element root ON root.content_id = b.content_id
 AND root.parent_index IS NULL;
