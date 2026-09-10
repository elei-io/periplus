# Continuous query optimization

This is the operating process for query performance work. Start here for each new
issue; use [QUERY.md](QUERY.md) for public semantics and compiler boundaries,
[SCHEMA.md](SCHEMA.md) for relation grain, and [the bench](../benchmarks/query/README.md)
for commands. Optimization is evidence-driven work, not a standing authorization
for production experiments or deployment. No recurring job is installed by this document.

The initial unattended campaign follows [QUERY_OPTIMIZATION_CAMPAIGN.md](QUERY_OPTIMIZATION_CAMPAIGN.md).

## Home and ownership

| Location | Responsibility |
| --- | --- |
| `benchmarks/query/` | Workload cases, benchmark protocol and sanitized baseline policy |
| `packages/periplus/src/periplus/query/benchmarking.py` | Measurement, profiles, result comparison; never imported by request handling |
| `packages/periplus/scripts/query_benchmark*.py` | Thin local and production-reader entrypoints |
| `packages/periplus/src/periplus/query/` | Production validation, diagnostics and semantics-preserving compiler changes |
| `docs/query-investigations/<issue>/` | Hypothesis, evidence, decision and production verification |
| `materialization/projections/` and public catalogue registry | Proven layout/materialization and public-view interventions in their existing owners |
| `UPSTREAM.md` | Minimal actionable DuckDB/DuckLake capability gaps |

There is one bench, not a second SQL engine or custom query extension. Research
SQL stays in the investigation directory until an intervention is accepted. Do
not move business projections into the benchmark module. Existing one-off probes
are supporting reproductions, not the standard operating workflow.

## Performance contracts

1. Known observations: resolve distinct content keys; fixed selected content should
   read approximately stable bytes/files as unrelated corpus grows. Propagating a
   predicate is insufficient if physical scans still read unrelated files.
2. Content discovery: selective supported predicates should use a candidate access
   path, followed by exact verification and selective extraction. Measure candidate
   amplification. An index cannot eliminate work proportional to a large match set.
3. Broad analytics: report expected scan cost explicitly. Arbitrary SQL has no
   constant-time guarantee. Row limits do not prove bounded upstream work.

Separate scope growth from unrelated corpus growth. `$scope=10,100,1000` measures
selected-scope growth only. A live lake changing between runs is observational
evidence, not a controlled scaling experiment.

## The loop

### 1. Identify and prioritize

Review the private Observatory query dashboard weekly and after engine/compiler,
catalogue, deployment or physical-layout changes. Also investigate timeout spikes,
user reports, and a few successful but expensive patterns. Use a fixed 7-day window
with operation=`execute`; separate public console, assistant, SDK, admin and prep.
Group by SQL fingerprint and engine/compiler version. Rank by total execution time,
frequency, timeout rate and user impact, not the slowest anecdote alone.

Record successful latency sample counts alongside p50/p95. Timeouts are censored
observations, not completed runtimes. Busy rejection is admission pressure, not SQL
execution latency. Check history delivery losses before treating counts as complete.
See [QUERY_HISTORY.md](QUERY_HISTORY.md); raw SQL, parameters and plan literals are
private and expire after 30 days. Do not export that history wholesale into Git.
Promote only reviewed, sanitized reproductions. Keep temporary private evidence in
ignored local artifacts and delete it when the investigation closes; do not extend
history retention by keeping exported payloads indefinitely.

### 2. Register the case before changing code

Create an investigation from [TEMPLATE.md](query-investigations/TEMPLATE.md) and a
case directory in `benchmarks/query/cases/`. Capture the incident ID, deployment,
limits, snapshot when available, plan preview completeness, workload family,
expected result semantics, and classification: schema/catalogue, optimizer, both,
or provisionally unclassified. Record physical maintenance/capacity as contributing
conditions rather than forcing every storage failure into the compiler category.
State one falsifiable hypothesis and its expected files/bytes/cardinality signature.

### Default optimizer development loop

Use the production test corpus directly: open one read transaction to freeze its
snapshot, run the query without the specific optimization, run it with that
optimization, require equal complete results, then require better measured
performance. Keep every unrelated setting identical. Repeat in reverse order to
check cache bias. The bench's `--content-scope` mode implements this for the research-only
content-scoping candidate; `--sql-override` supports other SQL experiments.
No corpus copy or ingestion pause is required. Separate scaling experiments do not
block this ordinary development loop.

### 3. Establish a baseline

Run a small read-only case first. Then run the representative case serially with an
explicit resource and time budget. Record access path (developer machine versus
production service), engine, catalogue definitions, effective settings, snapshot,
cache protocol and active maintenance. A new connection is not proof of cold S3,
OS, or service caches. Label first-connection and subsequent warm runs accurately.

Inspect public SQL, expanded views, compiler output and physical plan. Trace required
versus actual cardinality through scans, joins, aggregation, windows and sorting.
Read rows, files and bytes are different quantities. Missing metrics are unknown;
operator seconds across threads are not wall-time fractions. A timeout does not
produce a complete profile or an equivalence result.

### 4. Isolate one cause

For known-content access compare the same keys as literals, a key relation, and an
observation-derived join. If literals prune but joins do not, inspect propagation
and runtime filtering; if neither prunes, inspect layout, statistics and scan
capabilities. Separate candidate selection cost from extraction cost. Preserve
complete document partitions for section/window semantics.

For discovery vary selectivity while holding corpus fixed. For scaling hold selected
content, result semantics and resource settings fixed while adding unrelated data
in an isolated disposable corpus. Do not modify production to construct scale
ladders. Keep exact key sets fixed; hash-ranked top-N keys can change as ingestion
continues. Prototype only one intervention at a time and run both variant orders.

### 5. Decide at the responsible layer

| Evidence | Intervention | Acceptance evidence |
| --- | --- | --- |
| Incorrect grain, implicit repeated expansion or reconstruction | Correct catalogue semantics or justified fixed materialization | Semantic contract, rebuild/live parity, read/write/storage cost |
| Restriction lost in our compiler | Semantics-preserving rewrite | Duplicate/null/outer-join/window tests and reduced physical work |
| Native optimizer introduces avoidable producer work | Minimal upstream reproduction; narrowly proven local intervention if needed | Same-snapshot equality, multiple selectivities and unaffected families |
| Keys known but unrelated files read | Layout/statistics or engine access-path change | Files and bytes stable for fixed keys as corpus grows |
| Discovery has no usable candidate path | Predicate-specific index/searchable representation | No false negatives, exact verification, candidate and lookup cost |
| Files fragmented or maintenance blocked | LakeDucktor investigation | Maintenance progress and controlled remeasurement; no app file cleanup |
| Necessary work exceeds resources | Capacity or explicit workload limits/diagnostics | Memory/spill/concurrency envelope and honest expected cost |
| Insufficient evidence | Instrument/reproduce first | Completed profile or smaller isolating reproduction |

Do not materialize every `html_*` relation by default. Do not disable an optimizer
pass globally on the strength of one selective case. Do not rewrite substring
semantics into word search or URL substring matching into host classification.
Timeout increases are capacity policy, not an optimization acceptance criterion.

### 6. Validate and ship

Require result columns/types, values and multiplicities to agree at the same
snapshot; ordered cases also require deterministic ordering. Use adversarial local
fixtures for ties, nulls, duplicate captures, empty/full domains and section
boundaries. Digest comparison is a bounded regression check, not a formal proof.
Run targeted tests and `make check` after Python changes. Add a physical-work
regression where stable; keep noisy remote wall time out of unit-test assertions.

Record baseline/candidate results, unsuccessful variants, caveats, deployment plan
and rollback. Schema/projection work follows the existing generation lifecycle;
read-only production credentials cannot test those writes. No compiler change is
accepted solely because hand-written research SQL is fast: rerun unchanged public
SQL through the actual service after deployment. Compare limits, result caps and
snapshot differences before interpreting latency.

### 7. Close or continue

An issue is closed only after its stated acceptance conditions and production
verification pass. Otherwise record the remaining hypothesis and next experiment.
After deployment compare matching workload cohorts and timeout rates, including
unaffected cases. Retain sanitized regressions and decisions; expire private raw
artifacts. The next weekly review reopens regressions rather than repeating the
same unrecorded experiments.

## Execution-mode promotion

Develop compiler candidates only in experimental; stable begins with no custom
rewrites. Replay stable and experimental against the same frozen snapshot using
`benchmarks/query/`. Require equivalent results (including duplicates, nulls and
ordering guarantees), repeatable performance gains, and regression checks before
promoting a candidate to stable. Record mode and compiler version with evidence.
The public selector is for individual runs, not an automatic benchmark: consecutive
UI requests can see different snapshots and cache states. Catalogue and physical
layout changes affect both endpoints and require their own isolated experiments.
