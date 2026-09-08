-- Standalone DuckDB 1.5.5 reproduction. Run in a disposable in-memory database.
-- No Periplus, DuckLake, network, or persistent storage is required.
CREATE TABLE groups AS
SELECT md5(i::VARCHAR) AS account_id, 0::BIGINT AS lo, 100::BIGINT AS hi
FROM range(1000) t(i);
CREATE TABLE events AS
SELECT g.account_id, j AS position, j::BIGINT AS amount
FROM groups g CROSS JOIN range(100) t(j);
CREATE TABLE requested AS
SELECT account_id FROM groups LIMIT 8;
CREATE VIEW totals AS
SELECT g.account_id, sum(e.amount) AS total
FROM groups g LEFT JOIN events e
  ON e.account_id = g.account_id AND e.position >= g.lo AND e.position < g.hi
GROUP BY g.account_id;

-- Ordinary join to grouped derived data.
EXPLAIN ANALYZE
SELECT t.* FROM requested r JOIN totals t USING (account_id);

-- Same result; propagate requested keys below the aggregate and range join.
EXPLAIN ANALYZE
WITH selected_groups AS (
  SELECT g.* FROM groups g SEMI JOIN requested r USING (account_id)
), selected_events AS (
  SELECT e.* FROM events e SEMI JOIN requested r USING (account_id)
), scoped_totals AS (
  SELECT g.account_id, sum(e.amount) AS total
  FROM selected_groups g LEFT JOIN selected_events e
    ON e.account_id = g.account_id AND e.position >= g.lo AND e.position < g.hi
  GROUP BY g.account_id
)
SELECT t.* FROM requested r JOIN scoped_totals t USING (account_id);

-- Window variant: keep entire requested partitions, not selected rows within them.
CREATE VIEW ranked_events AS
SELECT *, row_number() OVER (PARTITION BY account_id ORDER BY position) AS rank
FROM events;
EXPLAIN ANALYZE
SELECT e.* FROM requested r JOIN ranked_events e USING (account_id) WHERE rank <= 2;
EXPLAIN ANALYZE
WITH selected_events AS (
  SELECT e.* FROM events e SEMI JOIN requested r USING (account_id)
), scoped_ranks AS (
  SELECT *, row_number() OVER (PARTITION BY account_id ORDER BY position) AS rank
  FROM selected_events
)
SELECT e.* FROM requested r JOIN scoped_ranks e USING (account_id) WHERE rank <= 2;
