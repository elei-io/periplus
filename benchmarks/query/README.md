# Public business-query baseline

See [the measured ClickHouse baseline](CLICKHOUSE_BASELINE.md) for the completed
retained-corpus run, feature gaps, physical work and next optimization order.

The 24 original cases were recovered unchanged from local branch
`codex/query-business-case-optimizations`; `origin.json` pins the source commit.
Their SQL, ordering rules, user stories and former budgets remain reviewable.
Two additional cases reproduce the user's element count and ten-row preview.
`single-capture-links-public` is a separately named, namespace-only port of the
original `experimental.capture/link` case; the original remains unchanged.
The former `classification` and memory/latency budgets describe the DuckDB
investigation; they are not current ClickHouse findings or settings overrides.

Run one case first through the normal public query service:

```sh
python3 benchmarks/query/run.py --case exact-page-history \
  --endpoint https://periplus.dev/api/query \
  --output .artifacts/query-clickhouse/page-history.jsonl
```

`--phase prep` records validation/EXPLAIN without executing the result query.
`--case all` explicitly requests the sequential suite. Execution defaults to one
initial and one warm run; rejected/truncated cases are not repeatedly executed.
`--scope` chooses a declared cohort size (the first historical size is the
default). The only SQL adaptation is converting `$scope` to positional binding;
question marks inside SQL literals are not parameters. No hash function, URL,
namespace, field or result predicate is silently substituted.

The runner stores query IDs, SQL plans, response status, timings, result metadata
and answer digests, not result rows. Truncation is not a complete answer. Empty
results, especially the literal `content_id='fixture'` diagnostic, are not proof
that extraction scales. Unordered LIMIT queries cannot guarantee identical
membership on repeated runs. A first run is not certified cold; do not flush
shared caches. API policy/reader limits stay unchanged.

Use a verified serving build for the measured campaign and record its recipe,
publication revision, counts, deployed image and effective reader limits. Collect
`system.query_log` metrics by returned query ID using read-only operator
inspection; missing metrics stay unknown. Failed queries need correlation with
the server log because the current failure response does not return query IDs.
Store physical rows/bytes read, peak memory, error code and stage along with the
API evidence. Validation failures, removed public interfaces, SQL dialect gaps,
resource rejections and execution cost are separate categories.

The public endpoint uses reader credentials and product limits. The admin native
SQL endpoint uses the privileged writer profile; its timings/budgets must never
be mixed into the public baseline. `corpus.sql` retains the small functional
smoke queries. See `docs/QUERY_OPTIMIZATION.md` for acceptance requirements.

Local runner checks: `python3 -m unittest discover -s benchmarks/query -p 'test_*.py'`.

Current cases use the document-centric public contract (`document_id`, `capture.url`). Older result reports used the earlier content/URL columns and are historical evidence, not measurements of this contract. Hash-range cohorts now refer to document identities and require fresh baselines.
