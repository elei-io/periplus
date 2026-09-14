# Continuous query optimization

This is the operating process for query performance work. Start here for each new
issue; use [QUERY.md](QUERY.md) for public semantics and compiler boundaries,
[SCHEMA.md](SCHEMA.md) for relation grain, and [the bench](../benchmarks/query/README.md)
for commands. Optimization is evidence-driven work, not a standing authorization
for production experiments or deployment. No recurring job is installed by this document.

The initial unattended campaign follows [QUERY_OPTIMIZATION_CAMPAIGN.md](QUERY_OPTIMIZATION_CAMPAIGN.md).

## Start here

1. Record the original query and outcome. Route service failures before investigating SQL.
2. Register one representative case in the existing bench and one short investigation.
3. State the expected work, one suspected cause, and what would count as fixed.
4. Compare the original and candidate on the same snapshot, then reverse their order.
5. Review correctness, performance, resource bounds and code simplicity; verify the
   unchanged user query through the service before closing the issue.

Use the [bench quick start](../benchmarks/query/README.md#quick-start) for commands.
This document owns the process and implementation rules; the bench README owns commands;
[QUERY.md](QUERY.md#performance-triage) owns schema/compiler boundaries. Investigation
records contain case-specific evidence, not additional general procedures.

## Route the outcome first

| Observed outcome | Next action |
| --- | --- |
| Service unavailable (503), connection or storage failure | Check pod termination reason, restart timing and memory limits early, alongside logs/history. A query-induced pod OOM can surface as 503. Record the phase; queries rejected before execution remain unevaluated for performance. |
| Admission rejection (429) | Investigate demand and admission pressure separately from query execution time. |
| Timeout or resource exhaustion | Record time to failure and effective limits; classify the cause only when evidence supports it. |
| Completed but slow, or unexpectedly broad work | Register a performance case even if the returned answer is small or empty. |
| Completed with an empty or unhelpful answer | Preserve the result; assess coverage and business meaning separately from execution success. |

For suspected pod OOMs, record the affected pod/container, termination reason (including
`OOMKilled` when reported), termination/restart timestamps, container memory limit,
DuckDB memory/spill settings, and available process/container memory measurements.
Correlate these with query IDs, execution times and concurrent work. A 503 or exit code
137 alone does not establish an OOM; `OOMKilled` establishes the termination reason,
not which query caused it. Missing query history after a kill does not exclude query involvement.

When evidence connects an OOM to query execution, investigate both the query's memory
work and why resource controls did not contain it. Compare the container limit with
whole-process memory, including candidate lookups, result buffering and runtime overhead;
the DuckDB memory setting is not a cap on all process allocations. Keep the schema/optimizer
cause provisional until plans or a bounded reproduction support it. Link availability and
query work in the same investigation instead of treating the 503 as unrelated infrastructure.
Acceptance must cover query completion within the agreed limits and service survival/recovery;
raising the pod limit alone is a capacity change, not proof of an optimization.

Group consecutive availability failures into an incident where evidence supports it.
Do not infer that the last query caused an outage, its duration, or the performance of
queries that never executed. Do not continue a heavy campaign through an outage;
resume only after recovery is established and the campaign's authorization permits it.
A business-case report is an observation record, not permission to retry its queries.

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

Preserve dated business-case SQL and outcomes. Promote selected cases into the existing
bench without narrowing predicates, joins, scope or limits. Link the originating case;
record execution success separately from whether the answer met the business need.
Define a latency/resource target and expected work for its family before experimenting.
For example, one selected capture producing millions of link rows is an investigation
trigger even when it completes; it is not by itself proof of a particular root cause.
Use the existing `query.sql` and supported `case.toml` fields. Put additional acceptance
criteria in the investigation rather than inventing unsupported configuration fields.
An isolating reproduction supports diagnosis; it never replaces the original acceptance query.

### Default optimizer development loop

Use the production test corpus directly: open one read transaction to freeze its
snapshot, run the query without the specific optimization, run it with that
optimization, require equal complete results, then require better measured
performance. Keep every unrelated setting identical. Repeat in reverse order to
check cache bias. Use the bench's `--optimization` option for registered passes and `--sql-override`
for research SQL. Candidate lookup cost remains inside each measured execution.
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
operator seconds across threads are not wall-time fractions. A separately executed
profile describes that execution, not the earlier request; large timing differences
require investigation rather than attributing the earlier latency to the later plan.
Use runner-recorded metadata instead of manual transcription where available. Missing
settings remain unknown; documentation defaults do not establish production limits.
A timeout does not produce a complete profile or an equivalence result.

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

### Implementation standard: simple, explicit modules

Apply these rules to new or changed optimizations. They are review requirements, not
claims that every existing optimization already provides the diagnostics below.

- Keep one coherent optimization in one named module under `query/optimizations/`. Use ordinary
  functions and the existing explicit compiler pass registry. Do not introduce a plugin loader, general rule
  engine, inheritance hierarchy or speculative optimizer framework.
- Make the flow readable: recognize supported SQL → resolve bounded candidates if
  needed → construct the SQL → execute. Keep recognition and transformation free of
  database I/O; put required lookups in an explicit function. Omit unused stages.
- Use typed inputs and small named results. Document supported shapes, resource bounds,
  and why the transformation preserves rows, duplicates, nulls and ordering. Comments
  explain semantic constraints rather than narrating syntax-tree manipulation.
- Keep transactions, deadlines, admission, cleanup and final execution in the service.
  Candidate lookups use that same transaction and deadline. An optimization must not
  create a connection, retry failed SQL or bypass limits.
- Report concise activation decisions through existing diagnostics/history: applied,
  unsupported shape, candidate budget exceeded, or catalogue mismatch, as applicable.
  Preparation distinguishes deferred lookup from an execution-time decision. Do not
  expose private SQL literals or keys. A routine decline preserves native execution;
  execution/storage errors remain errors, not silent declines.
- Keep SQL construction local and inspectable. Bind values where supported; use proper
  SQL AST construction for generated literals. Never interpolate untrusted strings.
- Extract a shared helper only for demonstrated duplication with the same semantics.
  Keep research machinery out of runtime imports. Remove superseded runtime paths and
  archive only useful sanitized research evidence when promoting a replacement.

A reviewer should be able to trace service → compiler → explicitly registered pass
through its module. Follow the [query developer guide](../packages/periplus/src/periplus/query/README.md)
for the current pass contract; do not add another dispatch mechanism. Tests cover recognition, meaningful
semantic edge cases, declines and service activation; avoid tests that merely duplicate
implementation steps. A simpler catalogue or engine fix remains preferable when it
addresses the proven cause.

### 6. Validate and ship

Require result columns/types, values and multiplicities to agree at the same
snapshot; ordered cases also require deterministic ordering. Use adversarial local
fixtures for ties, nulls, duplicate captures, empty/full domains and section
boundaries. Digest comparison is a bounded regression check, not a formal proof.
Run targeted tests and `make check` after Python changes. Add a physical-work
regression where stable; keep noisy remote wall time out of unit-test assertions.

Review four acceptance questions explicitly:

1. Are the complete answers equivalent, including required order and multiplicity?
2. Does the original workload meet its stated performance/work criteria?
3. Are resource bounds preserved and relevant neighboring workloads free of unacceptable regressions?
4. Can another developer explain the code, its limits and its removal path without a new abstraction?

A timeout baseline cannot prove equivalence. Use complete same-snapshot comparisons
on representative bounded fixtures for correctness, preserve the original timeout,
and require the original workload to complete within its acceptance limits. State the
limits of that evidence; never report a timed-out pair as equal or assign it a speedup.

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

Stable contains the promoted compiler baseline. Experimental includes that same
baseline plus explicitly gated new candidates.
Develop new candidates only in experimental, then replay stable and experimental
against the same frozen snapshot using
`benchmarks/query/`. Require equivalent results (including duplicates, nulls and
ordering guarantees), repeatable performance gains, and regression checks before
promoting a candidate to stable. Record mode and compiler version with evidence.
The public selector is for individual runs, not an automatic benchmark: consecutive
UI requests can see different snapshots and cache states. Catalogue and physical
layout changes affect both endpoints and require their own isolated experiments.


For the selected-content experiment, the user-approved acceptance objective is
reliable completion within existing limits, allowing modest latency regressions
for broader selections. Do not reject it solely for the recorded 6.2 → 9.9 second
synthetic broad-case regression. New timeouts/OOMs, incorrect results, or bypassed
resource bounds remain blockers. Record these tradeoffs with the performance gains.
