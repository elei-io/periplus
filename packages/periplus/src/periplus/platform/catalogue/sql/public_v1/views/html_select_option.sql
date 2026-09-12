CREATE OR REPLACE VIEW public_v1.html_select_option AS
WITH owned AS (
    SELECT o.content_id, o.node_index, o.subtree_end_index, o.attributes, o.text,
           max(s.node_index) AS select_node_index
    FROM public_v1.html_element o
    JOIN public_v1.html_element s ON s.content_id = o.content_id
     AND s.tag = 'select' AND s.namespace = 'http://www.w3.org/1999/xhtml'
     AND o.node_index > s.node_index AND o.node_index < s.subtree_end_index
    WHERE o.tag = 'option' AND o.namespace = 'http://www.w3.org/1999/xhtml'
    GROUP BY o.content_id, o.node_index, o.subtree_end_index, o.attributes, o.text
), indexed AS (
    SELECT *, (row_number() OVER (
        PARTITION BY content_id, select_node_index ORDER BY node_index
    ) - 1)::INTEGER AS option_index
    FROM owned
)
SELECT o.content_id, o.node_index, o.select_node_index, o.option_index,
       o.attributes['value'] AS value,
       o.text,
       map_contains(o.attributes, 'selected') AS selected,
       map_contains(o.attributes, 'disabled') AS disabled
FROM indexed o;
