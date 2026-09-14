-- Run only in an empty disposable DuckDB database; no Periplus or network required.
-- DuckDB 1.5.5: Unsupported join type for flattening correlated subquery.
CREATE TABLE requested(k INTEGER);
INSERT INTO requested VALUES (1);
CREATE TABLE items(k INTEGER);
INSERT INTO items VALUES (1), (1), (2);
WITH keys AS MATERIALIZED (SELECT * FROM requested)
SELECT o.k,
       (SELECT count(*) FROM items i SEMI JOIN keys r ON i.k=r.k WHERE i.k=o.k)
FROM keys o;
