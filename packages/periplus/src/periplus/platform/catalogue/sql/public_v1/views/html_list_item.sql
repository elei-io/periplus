CREATE OR REPLACE VIEW public_v1.html_list_item AS
WITH items AS (
    SELECT i.content_id, i.node_index, l.node_index AS list_node_index, i.subtree_end_index,
           (row_number() OVER (PARTITION BY i.content_id, l.node_index ORDER BY i.node_index) - 1)::INTEGER AS item_index,
           l.ordered, l.start_number, CASE WHEN l.reversed THEN -1 ELSE 1 END AS step,
           try_cast(regexp_extract(i.attributes['value'], '^[\t\n\f\r ]*([+-]?[0-9]+)', 1) AS BIGINT) AS declared_value
    FROM public_v1.html_list l
    JOIN public_v1.html_element i ON i.content_id = l.content_id AND i.parent_index = l.node_index
    WHERE i.tag = 'li' AND i.namespace = 'http://www.w3.org/1999/xhtml'
), numbered AS (
    SELECT *, CASE WHEN ordered THEN (
        coalesce(last_value(declared_value::HUGEINT - step::HUGEINT * item_index IGNORE NULLS) OVER (
            PARTITION BY content_id, list_node_index ORDER BY item_index
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ), start_number::HUGEINT) + step::HUGEINT * item_index
    )::BIGINT END AS ordinal
    FROM items
)
SELECT i.content_id, i.node_index, i.list_node_index, i.item_index, i.ordinal,
       material.element_text_excluding(i.content_id, i.node_index, ['ul','ol']) AS text
FROM numbered i;
