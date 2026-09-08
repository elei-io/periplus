CREATE OR REPLACE VIEW public_v1.html_jsonld AS
WITH scripts AS (
    SELECT s.content_id, s.node_index,
           coalesce(string_agg(n.value, '' ORDER BY n.node_index), '') AS source_text
    FROM public_v1.html_element s
    LEFT JOIN public_v1.html_node n ON n.content_id = s.content_id
     AND n.parent_index = s.node_index AND n.node_type = 'text'
    WHERE s.tag = 'script' AND s.namespace = 'http://www.w3.org/1999/xhtml'
      AND lower(trim(split_part(s.attributes['type'], ';', 1), chr(9) || chr(10) || chr(12) || chr(13) || ' ')) = 'application/ld+json'
    GROUP BY s.content_id, s.node_index
), parsed AS (
    SELECT content_id, node_index, source_text, try_cast(source_text AS JSON) AS value
    FROM scripts
)
SELECT content_id, node_index, value,
       CASE WHEN value IS NOT NULL THEN NULL
            WHEN trim(source_text, chr(9) || chr(10) || chr(12) || chr(13) || ' ') = ''
                THEN 'Empty JSON-LD script'
            ELSE 'Invalid JSON syntax' END::VARCHAR AS parse_error
FROM parsed;
