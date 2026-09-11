CREATE OR REPLACE MACRO public_v1.search(query VARCHAR) AS TABLE (
    WITH args AS MATERIALIZED (
        SELECT CASE WHEN length(query) > 256 THEN error('search query must be at most 256 characters')
                    ELSE lower(nfc_normalize(trim(regexp_replace(coalesce(query, ''), '[\t\n\r\f\x0b ]+', ' ', 'g')))) END AS needle
    ), fields AS (
        SELECT content_sha256 AS content_id, 1 AS weight, -1 AS source_index, NULL::VARCHAR AS title,
               -- Internal prose is already whitespace-collapsed and trimmed.
               nfc_normalize(text) AS body
        FROM material.prose
        UNION ALL
        SELECT content_sha256, CASE WHEN tag='title' THEN 6 ELSE 3 END, node_index,
               CASE WHEN tag='title' THEN text_direct END,
               nfc_normalize(trim(regexp_replace(
                   CASE WHEN tag='title' THEN text_direct ELSE attributes['content'] END,
                   '[\t\n\r\f\x0b ]+', ' ', 'g')))
        FROM material.html_nodes
        WHERE node_type='element' AND namespace='http://www.w3.org/1999/xhtml'
          AND (tag='title' OR (tag='meta' AND lower(attributes['name'])='description'))
    ), candidates AS (
        SELECT content_id, weight, source_index, title, body, strpos(lower(body), needle) AS position
        FROM fields, args WHERE needle <> ''
    ), hits AS (
        SELECT content_id, max(CASE WHEN position > 0 THEN weight ELSE 0 END)::DOUBLE AS score,
               first(title ORDER BY source_index) FILTER (WHERE weight=6) AS title,
               first(substr(body, greatest(position - 60, 1), 240)
                     ORDER BY weight DESC, source_index ASC) FILTER (WHERE position > 0) AS snippet
        FROM candidates GROUP BY content_id
        HAVING score > 0
    ), eligible AS MATERIALIZED (
        SELECT h.* FROM hits h
        WHERE EXISTS (SELECT 1 FROM public_v1.capture c WHERE c.content_id=h.content_id)
        ORDER BY score DESC, content_id ASC LIMIT 100
    ), representatives AS (
        SELECT content_id, coalesce(effective_url, requested_url) AS url
        FROM public_v1.capture
        WHERE content_id IN (SELECT content_id FROM eligible)
        QUALIFY row_number() OVER (
            PARTITION BY content_id ORDER BY captured_at DESC NULLS LAST, capture_id DESC
        ) = 1
    )
    SELECT h.content_id, h.title, c.url, h.snippet, h.score
    FROM eligible h JOIN representatives c USING(content_id)
    ORDER BY score DESC, content_id ASC
);
