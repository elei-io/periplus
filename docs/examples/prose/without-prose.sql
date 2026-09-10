-- Find captured book pages mentioning robots, then extract their titles.
-- Reconstruct searchable body text without using prose. The single-token
-- predicate is insensitive to whitespace collapsing; block separators still
-- prevent matches fabricated by joining words across block boundaries.
WITH source_content AS (
  SELECT DISTINCT content_id FROM public_v1.capture
  WHERE effective_url LIKE 'https://books.toscrape.com/catalogue/%/index.html'
    AND effective_url NOT LIKE 'https://books.toscrape.com/catalogue/category/%'
), source_nodes AS MATERIALIZED (
  SELECT n.content_id, n.node_index, n.subtree_end_index,
         n.node_type, n.name, n.namespace, n.value
  FROM public_v1.html_node n
  JOIN source_content s USING (content_id)
), marked AS (
  SELECT *,
    max(CASE WHEN node_type = 'element' AND name = 'body'
                  AND namespace = 'http://www.w3.org/1999/xhtml'
             THEN subtree_end_index END)
      OVER (PARTITION BY content_id ORDER BY node_index ROWS UNBOUNDED PRECEDING) AS body_end,
    max(CASE WHEN node_type = 'element'
                  AND name IN ('script', 'style', 'template', 'noscript')
             THEN subtree_end_index ELSE 0 END)
      OVER (PARTITION BY content_id ORDER BY node_index ROWS UNBOUNDED PRECEDING) AS excluded_end
  FROM source_nodes
), eligible AS MATERIALIZED (
  SELECT * FROM marked
  WHERE node_index < body_end AND node_index >= excluded_end
), blocks AS (
  SELECT * FROM eligible
  WHERE node_type = 'element'
    AND namespace = 'http://www.w3.org/1999/xhtml'
    AND name IN ('address', 'article', 'aside', 'blockquote', 'br', 'caption', 'dd', 'details', 'dialog', 'div', 'dl', 'dt', 'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hgroup', 'hr', 'li', 'main', 'menu', 'nav', 'ol', 'p', 'pre', 'section', 'summary', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'tr', 'ul')
), text_events AS (
  SELECT content_id, node_index AS position, 1 AS ordering, value AS text
  FROM eligible WHERE node_type = 'text'
  UNION ALL
  SELECT content_id, node_index, 0, ' ' FROM blocks
  UNION ALL
  SELECT content_id, subtree_end_index, 0, ' ' FROM blocks
), candidates AS (
  SELECT content_id FROM text_events
  GROUP BY content_id
  HAVING string_agg(text, '' ORDER BY position, ordering) ILIKE '%robot%'
)
SELECT DISTINCT c.effective_url AS url, h.text_direct AS title
FROM candidates m
JOIN public_v1.capture c USING (content_id)
JOIN public_v1.html_element h USING (content_id)
WHERE c.effective_url LIKE 'https://books.toscrape.com/catalogue/%/index.html'
  AND c.effective_url NOT LIKE 'https://books.toscrape.com/catalogue/category/%'
  AND h.tag = 'h1'
  AND h.namespace = 'http://www.w3.org/1999/xhtml'
ORDER BY url, title;
