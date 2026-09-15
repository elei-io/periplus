# Public query baselines

Run `corpus.sql` through the public query API against a verified build. Record
corpus size, recipe, elapsed time, rows/bytes read and peak memory, and compare
results before/after a change. These bounded functional cases replace the retired
DuckLake experiment harness. They do not establish large-scale performance.
See `docs/QUERY_OPTIMIZATION.md`.
