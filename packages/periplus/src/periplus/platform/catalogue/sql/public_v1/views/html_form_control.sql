CREATE OR REPLACE VIEW public_v1.html_form_control AS
WITH controls AS NOT MATERIALIZED (
    SELECT * FROM public_v1.html_element
    WHERE tag IN ('input','button','select','textarea','fieldset','output','object')
      AND namespace = 'http://www.w3.org/1999/xhtml'
), named_elements AS (
    SELECT * FROM public_v1.html_element
    WHERE map_contains(attributes, 'id') AND attributes['id'] <> ''
    QUALIFY row_number() OVER (
        PARTITION BY content_id, attributes['id'] ORDER BY node_index
    ) = 1
), owners AS (
    SELECT c.content_id, c.node_index,
           CASE WHEN named.tag = 'form' AND named.namespace = 'http://www.w3.org/1999/xhtml'
                THEN named.node_index END AS form_node_index
    FROM controls c
    LEFT JOIN named_elements named ON named.content_id = c.content_id
     AND named.attributes['id'] = c.attributes['form']
    WHERE map_contains(c.attributes, 'form')
    UNION ALL
    SELECT c.content_id, c.node_index, max(ancestor.node_index) AS form_node_index
    FROM controls c
    LEFT JOIN public_v1.html_element ancestor ON ancestor.content_id = c.content_id
     AND ancestor.tag = 'form' AND ancestor.namespace = 'http://www.w3.org/1999/xhtml'
     AND c.node_index > ancestor.node_index AND c.node_index < ancestor.subtree_end_index
    WHERE NOT map_contains(c.attributes, 'form')
    GROUP BY c.content_id, c.node_index
), textarea_text AS (
    SELECT c.content_id, c.node_index,
           coalesce(string_agg(n.value, '' ORDER BY n.node_index), '') AS text
    FROM controls c
    LEFT JOIN public_v1.html_node n ON n.content_id = c.content_id
     AND n.parent_index = c.node_index AND n.node_type = 'text'
    WHERE c.tag = 'textarea'
    GROUP BY c.content_id, c.node_index
)
SELECT c.content_id, c.node_index, o.form_node_index, c.tag,
       c.attributes['type'] AS type, c.attributes['name'] AS name,
       CASE WHEN c.tag = 'textarea' THEN t.text ELSE c.attributes['value'] END AS value,
       map_contains(c.attributes, 'required') AS required,
       map_contains(c.attributes, 'disabled') AS disabled,
       map_contains(c.attributes, 'readonly') AS readonly,
       map_contains(c.attributes, 'multiple') AS multiple
FROM controls c
JOIN owners o ON o.content_id = c.content_id AND o.node_index = c.node_index
LEFT JOIN textarea_text t ON t.content_id = c.content_id AND t.node_index = c.node_index;
