CREATE OR REPLACE MACRO public_v1.search(query VARCHAR) AS TABLE (
    WITH args AS MATERIALIZED (
        SELECT CASE WHEN length(query) > 256 THEN error('search query must be at most 256 characters')
                    ELSE lower(nfc_normalize(trim(regexp_replace(coalesce(query, ''), '[\t\n\r\f\x0b ]+', ' ', 'g')))) END AS needle
    ), bodies AS (
        -- Internal prose is already whitespace-collapsed and trimmed.
        SELECT content_sha256 AS content_id, nfc_normalize(text) AS body
        FROM material.prose
    ), hits AS (
        SELECT content_id, substr(body, greatest(strpos(lower(body), needle) - 60, 1), 240) AS snippet,
               1.0::DOUBLE AS score
        FROM bodies, args
        WHERE needle <> '' AND contains(lower(body), needle)
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
    ), titles AS (
        SELECT content_sha256 AS content_id, first(text_direct ORDER BY node_index) AS title
        FROM material.html_nodes
        WHERE node_type='element' AND tag='title' AND namespace='http://www.w3.org/1999/xhtml'
          AND content_sha256 IN (SELECT content_id FROM eligible)
        GROUP BY content_sha256
    )
    SELECT h.content_id, t.title, c.url, h.snippet, h.score
    FROM eligible h JOIN representatives c USING(content_id)
    LEFT JOIN titles t USING(content_id)
    ORDER BY score DESC, content_id ASC
);
