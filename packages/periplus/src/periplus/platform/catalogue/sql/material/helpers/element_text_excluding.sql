CREATE OR REPLACE MACRO material.element_text_excluding(source_content_id, source_node_index, excluded_tags) AS (
    WITH root AS (
        SELECT * FROM material.html_elements
        WHERE content_sha256=source_content_id AND node_index=source_node_index
    ), excluded AS (
        SELECT e.node_index, e.text_start, e.text_end
        FROM material.html_elements e, root r
        WHERE e.content_sha256=r.content_sha256
          AND e.node_index>r.node_index AND e.node_index<r.subtree_end_index
          AND list_contains(excluded_tags,e.tag) AND e.namespace='http://www.w3.org/1999/xhtml'
          AND NOT EXISTS (
              SELECT 1 FROM material.html_elements p
              WHERE p.content_sha256=e.content_sha256
                AND p.node_index>r.node_index AND p.node_index<e.node_index
                AND p.subtree_end_index>e.node_index
                AND list_contains(excluded_tags,p.tag) AND p.namespace='http://www.w3.org/1999/xhtml'
          )
    ), boundaries AS (
        SELECT node_index, text_start, text_end FROM excluded
        UNION ALL SELECT subtree_end_index, text_end, text_end FROM root
    ), gaps AS (
        SELECT b.node_index, r.text,
               coalesce(lag(b.text_end) OVER (ORDER BY b.node_index),r.text_start)-r.text_start+1 AS start_at,
               b.text_start-coalesce(lag(b.text_end) OVER (ORDER BY b.node_index),r.text_start) AS chars
        FROM boundaries b, root r
    )
    SELECT coalesce(string_agg(substr(text,start_at,chars),'' ORDER BY node_index),'') FROM gaps
);
