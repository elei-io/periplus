Current page-discovery and element-text cases are `search-discovery` and `element-text`.
Superseded public prose/term workloads are archived under `retired/`; they are not
part of the current public contract or normal case discovery. Historical experiment
results remain evidence for internal storage choices, not supported public examples.

# Query optimization bench

The disposable vocabulary/term-stat materialization experiment is documented in
[`docs/query-investigations/vocabulary-materialization/`](../../docs/query-investigations/vocabulary-materialization/README.md).
It builds only a temporary local lake and uses this bench's paired measurement
runner; it does not install production projections.
The proposed public `term` surface and real local HTML experiment are documented
in [term-surface.md](../../docs/query-investigations/vocabulary-materialization/term-surface.md).
The controlled literal-versus-term extraction growth test and join-planning
diagnostics are in [extraction-pruning.md](../../docs/query-investigations/vocabulary-materialization/extraction-pruning.md).
The experimental API rewrite and its benchmark results are documented in
[query-api-barrier.md](../../docs/query-investigations/vocabulary-materialization/query-api-barrier.md).
The 10/100/1,000 fixed-match growth, row-group and set-filter cutoff tests are in
[multikey-extraction.md](../../docs/query-investigations/vocabulary-materialization/multikey-extraction.md).

The process and decision matrix live in [QUERY_OPTIMIZATION.md](../../docs/QUERY_OPTIMIZATION.md).
This directory is the workload home. The runner is
`packages/periplus/src/periplus/query/benchmarking.py`; it runs standard DuckDB
against `public_v1.*`. No custom query extension is used.

## Local development

Run `make sync` first. Fresh worktrees also need the control-database setting for
backend test imports. Use the documented local-development value, never production
credentials, when running the checks:

```sh
PERIPLUS_CONTROL_DATABASE_URL=postgresql://periplus:periplus_local@127.0.0.1:55432/periplus make check
```

Environment-dependent integration tests remain skipped unless their isolated test
services are explicitly configured. With the disposable development lake configured:

```sh
make query-benchmark ARGS="--case exact-page-history --warm-runs 1 --seconds 30 --report ../../.artifacts/query-benchmarks/local.json"
```

The Make target supplies local-development defaults only. For any other lake,
provide the existing `PERIPLUS_DUCKLAKE_*` reader environment and run from
`packages/periplus/`:

```sh
uv run python scripts/query_benchmark.py --case exact-page-history \
  --warm-runs 1 --seconds 30 --report ../../.artifacts/query-benchmarks/reader.json
```

A case contains current public SQL and `case.toml`: user story, provisional
classification, deterministic ordering policy, optional `$scope` scales, memory
budget, total execution deadline and warm latency target. Explicit `--case` is
required; the command never starts the whole production workload implicitly.

## Production corpus from a developer machine

The existing homelab operator wrapper supplies the cluster context. From the
homelab checkout (adjust the Periplus checkout path for your machine):

```sh
make run CMD="python /Users/ekku/Code/elei/periplus/packages/periplus/scripts/query_benchmark_production.py --case exact-page-history --warm-runs 1 --seconds 30 --report /Users/ekku/Code/elei/periplus/.artifacts/query-benchmarks/production.json"
```

Prerequisites: homelab operator access, `kubectl`, a synced Periplus `.venv`, and
network access to the configured S3 endpoint. This adapter reads the query
Deployment's lake/settings environment and only the two existing lake-reader
Secrets. It opens a temporary localhost PostgreSQL forward, passes credentials
only through the child environment and closes the forward on exit. It does not
create infrastructure, modify grants, change configuration, load writer credentials
or write to the lake. It suppresses native stderr because storage errors may
contain connection secrets. Setup failures expose only exception types.

Each invocation is sequential and capped at 15 minutes externally. Use one small
case first; run broad cases explicitly. Each measurement has a 1–120 second total
DuckDB execution deadline, including normal execution and warm profiles, plus
100,000-row / 32 MiB canonical-result bounds. Environment settings override bench
defaults and effective settings are recorded. Connection setup is outside the SQL
deadline; the production wrapper's process deadline covers setup too. Local trusted
research SQL is not a public sandbox; do not execute unreviewed SQL with this tool.

This is **local execution against production data**, not production-service latency.
The metadata tunnel and S3 network path differ. The runner does not apply QueryService
rewrites, row caps or admission. Production acceptance must separately execute the
unchanged public query through the ordinary authenticated query service and record
its policy, deployment and snapshot. Do not run heavy reader probes concurrently
with that verification.

## Controlled comparisons

The normal protocol is one execution with bounded result collection, then 1–3
warm `EXPLAIN (ANALYZE, FORMAT JSON)` runs in the same read transaction. The first
run is not guaranteed cold. Reports contain result digests, not result rows, plus
snapshot, catalogue-definition digest, engine and effective settings. Scan and
blocking-operator summaries describe the last completed warm profile; scalar
metrics cover every warm run. The timeout artifact marks the run incomplete and
records a safe exception type; partial work is never considered equivalent.

For a research SQL rewrite, baseline and candidate share one transaction/snapshot:

```sh
uv run python scripts/query_benchmark.py --case exact-page-history \
  --sql-override /absolute/path/candidate.sql --warm-runs 1 --seconds 60 \
  --report ../../.artifacts/query-benchmarks/pair.json
```

Repeat with `--candidate-first` to expose order/cache effects. Each variant receives
its own total deadline. `--baseline PATH` compares separate artifacts but rejects
snapshot differences; it cannot pin a past snapshot. Settings/catalogue/engine
mismatches also fail comparison. A live lake may advance between invocations, so
use the paired mode for SQL experiments. For code or physical-layout experiments,
use an isolated retained corpus with fixed inputs; the bench does not provision a
clone, freeze retention, or promise that historic lake files remain available.

`--scale` selects a declared case scale. It is not an unrelated-corpus growth test.
For that test build fixed selected keys and several isolated corpus sizes, holding
layout protocol and resources constant. Neither top-N sampling on a moving lake
nor timings across compaction snapshots proves scaling behavior.

## Artifacts and regression policy

Reports may contain SQL and predicate literals in profile summaries. Keep them
private under ignored `.artifacts/query-benchmarks/`, delete private exports when
closing an investigation, and commit only reviewed sanitized summaries. Baseline
acceptance requires complete results with matching columns/types and row digest
(including duplicates and ordered sequence where declared), representative nonempty
coverage, and the case's physical-work/latency criteria. An empty smoke test checks
connectivity, not workload performance. Record missing profiler metrics as unavailable;
do not infer pruning from low result cardinality or cached zero bytes alone.

Do not silently refresh a baseline after a regression. Explain version, corpus,
layout and semantic changes in the associated investigation. Compiler fixes must
ultimately accelerate unchanged public SQL; an override alone is not a shipped fix.

## Bench verification (2026-09-10)

The production reader command completed a nonempty `current-domain-inventory`
case at snapshot 61137 (one row; 4.59 s first execution, 145 ms warm profile),
with two threads, approximately 512 MB memory and 256 MB spill. These are
connectivity/profiling smoke measurements, not an optimization claim. A separate
candidate-first comparison at snapshot 61008 used the same SQL on both sides and
confirmed complete matching results. Native deadline and result-bound tests, all
seven current-case bind checks, and same-transaction pairing are covered by the
focused benchmark tests. Reports remain ignored local artifacts.

## Default optimizer experiment: production snapshot, off versus on

Use the production corpus directly. A single read transaction freezes the snapshot
for both variants while ingestion continues. For the general research content-scoping candidate (runtime activation is separately restricted):

```sh
uv run python scripts/query_benchmark.py --case gov-heading-sections \
  --content-scope --warm-runs 1 --seconds 120 \
  --report ../../.artifacts/query-benchmarks/scope-pair.json
```

Pass the same arguments to `query_benchmark_production.py` through the homelab
operator wrapper for production reader access. The baseline executes original SQL;
the candidate applies our content-scope transformation after verifying the installed
catalogue supports it. All native DuckDB optimizer settings remain identical.
An ineligible case fails rather than silently comparing the query to itself.

Require complete equal results first, then evaluate latency and physical-work
improvement. Repeat with `--candidate-first` to check cache/order bias. Neither a
partial result nor a timeout is a correctness pass. A separate isolated corpus is
needed only for controlled corpus-growth or physical-write experiments, not for
this normal optimizer development loop.

`--content-scope` exits unsuccessfully if results differ, measurement is incomplete,
or the candidate's measured warm median is not lower than the baseline's. The
report retains both timings and their ratio. Treat one speedup as provisional until
reverse-order measurements corroborate it; this is an experiment gate, not a noisy
wall-time assertion in unit tests.

Interrupted measurements retain a safe `progress` record: case/scale, snapshot,
failing phase, completed normal-execution timing and row count, and completed warm
profile count. Paired failures also identify the failed variant and retain any
fully measured variant under `completed_variants`. The overall report remains
`complete=false` with no equivalence or speedup claim. Native exception messages
are never recorded because they can contain credential-bearing storage URLs.

### Separating execution from profiling

For paired research runs, `--ordinary-warm-runs` repeats the ordinary SQL rather
than `EXPLAIN ANALYZE`. It records `warm_protocol=ordinary_execution`, verifies
each repeat against the complete first result, and leaves physical profile metrics
empty. Use this when profiling itself exceeds the bounded budget; do not compare
these timings with profiled timings or claim absent scan metrics are zero.
`--access-path cluster_direct_reader` labels a temporary in-cluster read-only
bench explicitly. Keep the same snapshot, limits and execution protocol on both sides.

`--warm-runs 0` records one ordinary execution per variant. The warm median is
null and acceptance uses `normal_time_ratio`; repeat the pair in reverse order.
This mode is useful when one baseline finishes within the measurement deadline
but a baseline plus a repeat cannot. It does not establish a warm-cache speedup.

### Experimental selected-content execution

With the normal lake-reader environment, use the shared measurement/result checks
and include execution-time key selection in each candidate measurement:

```sh
uv run python scripts/query_selected_content_benchmark.py \
  --case selected-content-headings --report ../../.artifacts/selected-forward.json
uv run python scripts/query_selected_content_benchmark.py \
  --case selected-content-headings --candidate-first \
  --report ../../.artifacts/selected-reverse.json
```

Each pair pins one transaction and checks installed definitions. These are ordinary
executions, not profiles; no files/bytes metrics are claimed. Reports contain
bounded result digests and metadata, not SQL or keys. An incomplete original query
is recorded as failure, never equality. For private incident SQL, create the case
under ignored `.artifacts/` with a matching case.toml and pass `--case-root`; remove
it after the investigation. Public-service activation and limits still require
separate QueryService validation.

## Unified node layout experiment

Run from packages/periplus:

```sh
uv run python ../../benchmarks/query/experiments/node_layout.py --documents 500 --report ../../.artifacts/query-benchmarks/node-layout.json
```

Use `--input-dir` for a retained UTF-8 HTML corpus; without it, input is explicitly
synthetic. The disposable lake compares old separate element/node storage with the
unified node table using the shared measurement runner in both orders.
See [the investigation](../../docs/query-investigations/node-layout/README.md).

## Content summaries and positional postings

The disposable comparison in
[posting-summary](../../docs/query-investigations/posting-summary/README.md)
measures existing production content/node discovery and isolated flat/packed
positional layouts. It checks full-set fingerprints in both variant orders,
retains ICU token provenance, and separates fixed rare matches from growing common
matches under appends. It installs no production materializations.
