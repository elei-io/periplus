CREATE OR REPLACE MACRO content.subtree_text(
    source_content_id VARCHAR, root_element_index INTEGER,
    max_chars := 20000, max_elements := 10000
) AS TABLE (
    WITH args AS (
        SELECT source_content_id AS wanted_content,
               CASE WHEN root_element_index IS NOT NULL AND root_element_index >= 0
                    THEN root_element_index ELSE error('element_index must be non-negative') END AS wanted_index,
               CASE WHEN max_chars IS NOT NULL AND max_chars BETWEEN 0 AND 100000
                    AND max_chars = trunc(max_chars)
                    THEN max_chars::BIGINT ELSE error('max_chars must be an integer from 0 to 100000') END AS char_limit,
               CASE WHEN max_elements IS NOT NULL AND max_elements BETWEEN 1 AND 10000
                    AND max_elements = trunc(max_elements)
                    THEN max_elements::BIGINT ELSE error('max_elements must be an integer from 1 to 10000') END AS node_limit
    ), root AS MATERIALIZED (
        SELECT e.element_index AS root_index,
               CASE WHEN e.subtree_end_index - e.element_index <= a.node_limit
                    THEN e.subtree_end_index
                    ELSE error('subtree exceeds max_elements; select a smaller root') END AS root_end,
               a.wanted_content, a.char_limit
        FROM content.html_element e, args a
        WHERE e.content_id = a.wanted_content AND e.element_index = a.wanted_index
    ), nodes AS MATERIALIZED (
        SELECT e.element_index, e.subtree_end_index, e.depth, e.text_direct, e.text_tail,
               r.root_index, r.root_end, r.char_limit
        FROM content.html_element e, root r
        WHERE e.content_id = r.wanted_content
          AND e.element_index >= r.root_index AND e.element_index < r.root_end
    ), events AS (
        SELECT n.element_index AS position, 1 AS phase, n.depth,
               coalesce(n.text_direct, '') AS piece, n.char_limit FROM nodes n
        UNION ALL
        SELECT n.subtree_end_index AS position, 0 AS phase, n.depth,
               coalesce(n.text_tail, '') AS piece, n.char_limit FROM nodes n
        WHERE n.element_index <> n.root_index
    ), ordered AS (
        SELECT *, coalesce(sum(length(piece)) OVER (
            ORDER BY position, phase, depth DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ), 0)::BIGINT AS preceding_chars
        FROM events
    )
    SELECT coalesce(string_agg(
               CASE WHEN preceding_chars < r.char_limit
                    THEN substr(piece, 1, (r.char_limit - preceding_chars)::BIGINT)
                    ELSE '' END, '' ORDER BY position, phase, depth DESC), '') AS text,
           coalesce(sum(length(piece)), 0)::BIGINT > r.char_limit AS truncated,
           coalesce(sum(length(piece)), 0)::BIGINT AS total_chars,
           (r.root_end - r.root_index)::BIGINT AS element_count
    FROM root r LEFT JOIN ordered ON true
    GROUP BY r.root_index, r.root_end, r.char_limit
);
