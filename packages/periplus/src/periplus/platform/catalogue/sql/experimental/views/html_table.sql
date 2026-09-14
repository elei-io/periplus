CREATE OR REPLACE VIEW experimental.html_table AS
WITH captions AS (
    SELECT * FROM experimental.html_element
    WHERE tag = 'caption' AND namespace = 'http://www.w3.org/1999/xhtml'
    QUALIFY row_number() OVER (PARTITION BY content_id, parent_index ORDER BY node_index) = 1
)
SELECT t.content_id, t.node_index, c.node_index AS caption_node_index,
       CASE WHEN c.node_index IS NOT NULL THEN
           material.element_text_excluding(c.content_id, c.node_index, ['table']) END AS caption
FROM experimental.html_element t
LEFT JOIN captions c ON c.content_id=t.content_id AND c.parent_index=t.node_index
WHERE t.tag='table' AND t.namespace='http://www.w3.org/1999/xhtml';
