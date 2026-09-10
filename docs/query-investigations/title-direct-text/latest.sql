WITH ranked AS (
 SELECT capture_id, effective_url, captured_at, content_id,
        row_number() OVER (PARTITION BY effective_url ORDER BY captured_at DESC, capture_id DESC) AS rn
 FROM public_v1.capture
 WHERE effective_url LIKE '%.gov%' AND content_id >= '0' AND content_id < '1'
)
SELECT r.capture_id, r.effective_url, r.captured_at, m.node_index,
       left(m.value, 120) AS page_title
FROM ranked r
LEFT JOIN (SELECT * FROM (WITH elements AS NOT MATERIALIZED (
    SELECT * FROM public_v1.html_element
    WHERE namespace = 'http://www.w3.org/1999/xhtml'
)
SELECT e.content_id, e.node_index, 'title' AS kind, 'title' AS name,
       e.text_direct AS value
FROM elements e
WHERE e.tag = 'title'
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
FROM elements WHERE tag = 'html' AND map_contains(attributes, 'lang')) WHERE content_id >= '0' AND content_id < '1') m ON m.content_id=r.content_id AND m.kind='title'
WHERE r.rn=1
ORDER BY r.captured_at DESC, r.capture_id DESC, m.node_index
LIMIT 20;
