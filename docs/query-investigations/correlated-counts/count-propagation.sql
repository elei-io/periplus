-- Empty disposable DuckDB database only. No Periplus, DuckLake or network.
CREATE TABLE requested(k INTEGER);
INSERT INTO requested VALUES (7), (1001);
CREATE TABLE items AS SELECT (i % 1000)::INTEGER k FROM range(10000) r(i);
-- Both queries return (7,10), (1001,0). Inspect aggregate output cardinalities.
EXPLAIN ANALYZE
SELECT o.k, (SELECT count(*) FROM items i WHERE i.k=o.k) AS n FROM requested o;
EXPLAIN ANALYZE
WITH counts AS MATERIALIZED (
 SELECT i.k, count(*) n FROM items i SEMI JOIN requested r ON i.k=r.k GROUP BY i.k
)
SELECT o.k, coalesce(c.n,0) n FROM requested o LEFT JOIN counts c ON c.k=o.k;
