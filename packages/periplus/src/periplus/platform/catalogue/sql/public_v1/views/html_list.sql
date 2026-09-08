CREATE OR REPLACE VIEW public_v1.html_list AS
SELECT l.content_id, l.node_index, l.tag = 'ol' AS ordered,
       CASE WHEN l.tag = 'ol' THEN coalesce(
           try_cast(regexp_extract(l.attributes['start'], '^[\t\n\f\r ]*([+-]?[0-9]+)', 1) AS BIGINT),
           CASE WHEN map_contains(l.attributes, 'reversed') THEN count(i.node_index) ELSE 1 END
       ) END::BIGINT AS start_number,
       l.tag = 'ol' AND map_contains(l.attributes, 'reversed') AS reversed
FROM public_v1.html_element l
LEFT JOIN public_v1.html_element i ON i.content_id = l.content_id
 AND i.parent_index = l.node_index AND i.tag = 'li'
 AND i.namespace = 'http://www.w3.org/1999/xhtml'
WHERE l.tag IN ('ul', 'ol') AND l.namespace = 'http://www.w3.org/1999/xhtml'
GROUP BY l.content_id, l.node_index, l.tag, l.attributes;
