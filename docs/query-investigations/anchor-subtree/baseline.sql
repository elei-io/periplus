CREATE OR REPLACE MACRO public_v1.subtree_text(
    source_content_id VARCHAR, root_node_index INTEGER,
    max_chars := 20000, max_nodes := 10000
) AS TABLE (
    WITH args AS (
        SELECT source_content_id AS wanted_content,
               CASE WHEN root_node_index IS NOT NULL AND root_node_index >= 0
                    THEN root_node_index ELSE error('node_index must be non-negative') END AS wanted_index,
               CASE WHEN max_chars IS NOT NULL AND max_chars BETWEEN 0 AND 100000
                    AND max_chars = trunc(max_chars)
                    THEN max_chars::BIGINT ELSE error('max_chars must be an integer from 0 to 100000') END AS char_limit,
               CASE WHEN max_nodes IS NOT NULL AND max_nodes BETWEEN 1 AND 10000
                    AND max_nodes = trunc(max_nodes)
                    THEN max_nodes::BIGINT ELSE error('max_nodes must be an integer from 1 to 10000') END AS node_limit
    ), root AS MATERIALIZED (
        SELECT n.node_index AS root_index,
               CASE WHEN n.subtree_end_index - n.node_index <= a.node_limit
                    THEN n.subtree_end_index
                    ELSE error('subtree exceeds max_nodes; select a smaller root') END AS root_end,
               a.wanted_content, a.char_limit
        FROM public_v1.html_node n, args a
        WHERE n.content_id = a.wanted_content AND n.node_index = a.wanted_index
    ), text_nodes AS (
        SELECT n.node_index, n.value AS piece, r.char_limit
        FROM public_v1.html_node n, root r
        WHERE n.content_id = r.wanted_content
          AND n.node_index >= r.root_index AND n.node_index < r.root_end
          AND n.node_type = 'text'
    ), ordered AS (
        SELECT *, coalesce(sum(length(piece)) OVER (
            ORDER BY node_index ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ), 0)::BIGINT AS preceding_chars FROM text_nodes
    )
    SELECT coalesce(string_agg(
               CASE WHEN preceding_chars < r.char_limit
                    THEN substr(piece, 1, (r.char_limit - preceding_chars)::BIGINT)
                    ELSE '' END, '' ORDER BY node_index), '') AS text,
           coalesce(sum(length(piece)), 0)::BIGINT > r.char_limit AS truncated,
           coalesce(sum(length(piece)), 0)::BIGINT AS total_chars,
           (r.root_end - r.root_index)::BIGINT AS node_count
    FROM root r LEFT JOIN ordered ON true
    GROUP BY r.root_index, r.root_end, r.char_limit
);
