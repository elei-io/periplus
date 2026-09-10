# Query optimization bench

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
for both variants while ingestion continues. For the research-only content-scoping candidate (removed from the public API):

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
