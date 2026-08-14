# Periplus query benchmarks

These cases measure the SQL a user actually writes against the public `web.*` and `content.*`
catalogue. Production optimizations remain in the native extension; the Python runner only invokes
DuckDB, captures profiles, and compares results.

Each directory under `cases/` contains:

- `query.sql`: directly executable public SQL, optionally using the bound `$scope` parameter;
- `case.toml`: the user story, classification, scale ladder, memory limit, and completion budget.

Run one normal execution followed by two warm `EXPLAIN ANALYZE` executions:

```sh
make query-benchmark ARGS="--case current-page-image-accessibility --scale 100 \
  --report ../.artifacts/query-benchmarks/image-accessibility.json"
```

To compare a candidate with an earlier artifact, add `--baseline PATH`. The command fails when the
column contract, row count, result multiplicity, values, or specified ordering differs. Performance
ratios are recorded but do not become noisy local correctness failures.

During a measured implementation spike, `--sql-override PATH` may run one checked-in research SQL
variant under the same case identity and compare it to the public-query baseline. The report records
the override path and SQL. A result is accepted only after the unchanged `query.sql` receives the
optimization through the native extension.

Commit baselines only for a pinned DuckLake snapshot and matching DuckDB, extension, and catalogue
versions. Ad-hoc reports belong in `.artifacts/query-benchmarks/`. The JSON report records the
snapshot and versions needed to interpret a result.

The fixed protocol is:

1. execute the public SQL normally once;
2. execute `EXPLAIN (ANALYZE, FORMAT JSON)` warm;
3. record scans and cardinality before and after every blocking operator;
4. compare exact results before interpreting performance;
5. measure both selected-scope growth and fixed-scope behavior as unrelated corpus data grows.

The `current-page-image-accessibility` case is the next optimization candidate. It asks a normal
content-audit question while testing whether a bounded runtime `content_id` scope can constrain a
direct `content.html_element` join.
