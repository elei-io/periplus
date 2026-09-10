WITH latest AS (
 SELECT capture_id, content_id, effective_url, captured_at,
        row_number() OVER (PARTITION BY effective_url ORDER BY captured_at DESC, capture_id DESC) AS rn
 FROM public_v1.capture
 WHERE effective_url LIKE '%.gov%' AND content_id >= '0' AND content_id < '1'
)
SELECT c.capture_id, c.effective_url, c.captured_at, j.node_index, j.value, j.parse_error
FROM latest c LEFT JOIN (SELECT * FROM (WITH scripts AS (
    SELECT s.content_id, s.node_index,
           s.text_direct AS source_text
    FROM public_v1.html_element s
    WHERE s.tag = 'script' AND s.namespace = 'http://www.w3.org/1999/xhtml'
      AND lower(trim(split_part(s.attributes['type'], ';', 1), chr(9) || chr(10) || chr(12) || chr(13) || ' ')) = 'application/ld+json'
), parsed AS (
    SELECT content_id, node_index, source_text, try_cast(source_text AS JSON) AS value
    FROM scripts
)
SELECT content_id, node_index, value,
       CASE WHEN value IS NOT NULL THEN NULL
            WHEN trim(source_text, chr(9) || chr(10) || chr(12) || chr(13) || ' ') = ''
                THEN 'Empty JSON-LD script'
            ELSE 'Invalid JSON syntax' END::VARCHAR AS parse_error
FROM parsed) WHERE content_id >= '0' AND content_id < '1') j ON j.content_id = c.content_id
WHERE c.rn = 1;
