-- Standalone, disposable in-memory DuckDB 1.5.5 reproduction.
-- No production credentials, DuckLake, or Periplus objects are needed.
CREATE TABLE captures AS SELECT i::INTEGER AS content_id, 'url-' || i AS url FROM range(1000) t(i);
CREATE TABLE elements AS SELECT c.content_id, j::INTEGER AS node_index, (j+10)::INTEGER AS subtree_end_index,
 'h1' AS tag FROM captures c CROSS JOIN range(0,100,10) t(j);
CREATE TABLE nodes AS SELECT c.content_id, j::INTEGER AS node_index, 'text' AS value FROM captures c CROSS JOIN range(100) t(j);
CREATE VIEW combined AS
WITH selected AS MATERIALIZED (SELECT * FROM captures WHERE url='url-42'),
keys AS MATERIALIZED (SELECT DISTINCT content_id FROM selected),
scoped_elements AS NOT MATERIALIZED (SELECT e.* FROM elements e SEMI JOIN keys USING(content_id)),
scoped_nodes AS NOT MATERIALIZED (SELECT n.* FROM nodes n SEMI JOIN keys USING(content_id)),
headings AS (
 SELECT h.content_id,h.node_index,h.tag,string_agg(n.value,'') AS text FROM scoped_elements h
 LEFT JOIN scoped_nodes n ON n.content_id=h.content_id AND n.node_index>h.node_index AND n.node_index<h.subtree_end_index
 WHERE h.tag IN ('h1','h2','h3','h4','h5','h6') GROUP BY h.content_id,h.node_index,h.tag
), sections AS (
 SELECT content_id,node_index,lead(node_index) OVER(PARTITION BY content_id ORDER BY node_index) AS next_heading
 FROM scoped_elements WHERE tag IN ('h1','h2','h3','h4','h5','h6')
)
SELECT c.url,h.*,s.next_heading FROM selected c JOIN headings h USING(content_id)
JOIN sections s ON s.content_id=h.content_id AND s.node_index=h.node_index;

-- DuckDB 1.5.5: common-subplan sharing builds all 10,000 element rows.
-- Its two consumers restrict to the selected content only after CTE_SCAN.
EXPLAIN ANALYZE SELECT * FROM combined;

-- Diagnostic only: each element scan now emits 10 rows with a dynamic key filter.
-- The result multiset is unchanged; neither query promises output ordering.
SET disabled_optimizers = 'common_subplan';
EXPLAIN ANALYZE SELECT * FROM combined;
SET disabled_optimizers = '';
