WITH elements AS NOT MATERIALIZED (
    SELECT * FROM public_v1.html_element
    WHERE namespace = 'http://www.w3.org/1999/xhtml'
)
SELECT e.content_id, e.node_index, 'title' AS kind, 'title' AS name,
       coalesce(string_agg(n.value, '' ORDER BY n.node_index), '') AS value
FROM elements e
LEFT JOIN public_v1.html_node n ON n.content_id = e.content_id
 AND n.node_index > e.node_index AND n.node_index < e.subtree_end_index
 AND n.node_type = 'text'
WHERE e.tag = 'title'
GROUP BY e.content_id, e.node_index
UNION ALL
SELECT content_id, node_index, 'meta_name', attributes['name'], attributes['content']
FROM elements WHERE tag = 'meta' AND map_contains(attributes, 'name')
UNION ALL
SELECT content_id, node_index, 'meta_property', attributes['property'], attributes['content']
FROM elements WHERE tag = 'meta' AND map_contains(attributes, 'property')
UNION ALL
SELECT content_id, node_index, 'meta_http_equiv', attributes['http-equiv'], attributes['content']
FROM elements WHERE tag = 'meta' AND map_contains(attributes, 'http-equiv')
UNION ALL
SELECT content_id, node_index, 'meta_charset', 'charset', attributes['charset']
FROM elements WHERE tag = 'meta' AND map_contains(attributes, 'charset')
UNION ALL
SELECT content_id, node_index, 'link_rel',
       unnest(list_distinct(list_filter(regexp_split_to_array(attributes['rel'], '[\t\n\f\r ]+'),
                                      token -> token <> ''))),
       attributes['href']
FROM elements WHERE tag = 'link' AND map_contains(attributes, 'rel')
UNION ALL
SELECT content_id, node_index, 'html_attribute', 'lang', attributes['lang']
FROM elements WHERE tag = 'html' AND map_contains(attributes, 'lang');
