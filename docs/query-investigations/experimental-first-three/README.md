# First experimental compiler candidates

Classification: compiler/optimizer for propagation, shared intermediates and staged extraction; underlying scan amplification is also a physical-layout issue.

1. Propagate selected capture content IDs into all HTML inputs using the existing content-scoping prototype.
2. On the same scoped heading/section query, materialize restricted primitive inputs to prevent native common-subplan extraction from constructing unrestricted HTML producers. Keep native optimizer configuration unchanged.
3. Discover exact matching headings first, then scope section primitives by their distinct content IDs. Preserve complete per-document heading order when calculating section boundaries. Prose is not used as an approximate substitute.

Protocol: production read-only paired bench, one transaction per baseline/candidate pair, both orders, complete typed result digest equality, ordinary execution plus warm profiles, unchanged settings. Start with one known URL and widen only when the pair completes. No performance or deployment claim until measured.

API activation must be experimental-only and conservative, with unchanged original SQL, actual executed-plan evidence, bounded compilation/definition validation, and correctness tests. Stable and shared catalogue/layout remain unchanged.

## Evidence (2026-09-10)

Runs used a temporary read-only homelab reader pod, the production corpus, DuckDB
1.5.5, DuckLake d8a1881e, two threads, 512 MB DuckDB memory and 256 MB spill.
Each pair pinned one snapshot. Native optimizers remained enabled. The initial
pod image was 1ff62bc with the research Python modules copied in; subsequent
main-branch janitor fixes do not change these query definitions.

| Comparison | Order | Snapshot | Baseline ordinary | Candidate ordinary | Baseline repeat | Candidate repeat | Full typed results |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| Exact URL → headings | Baseline first | 95667 | 96.95 s | 7.36 s | — | — | Equal, 3 rows |
| Exact URL → headings | Candidate first | 95787 | 88.88 s | 5.12 s | — | — | Equal, 3 rows |
| Exact URL → headings, second site | Baseline first | 96085 | 101.76 s | 5.07 s | — | — | Equal, 2 rows |
| Scoped inline → scoped materialized section inputs | Baseline first | 95584 | 32.78 s | 4.19 s | 46.05 s | 3.35 s | Equal, 3 rows |
| Scoped inline → scoped materialized section inputs | Candidate first | 95863 | 35.92 s | 7.71 s | 47.13 s | 0.88 s | Equal, 3 rows |

The heading pairs use `--warm-runs 0`; these are ordinary execution comparisons,
not warm-profile speedups. The section isolation pairs use
`--ordinary-warm-runs --warm-runs 1`; repeats execute SQL without EXPLAIN ANALYZE.
All complete comparisons matched settings, catalogue digest, engine/extensions,
column names/types and complete ordered result digest. The heading digest was
`f67b337f0924febd6f1b870d33a9acc1c6d5b14db25825f9a0bf8219b07f2b9e`.

Failed attempts are part of the result:

- A developer-machine baseline did not complete within 120 seconds (snapshot 94711).
- Cluster heading baselines returned 3 rows in 43.48/71.74/95.47 seconds, but
  their additional profiled or ordinary repeats exceeded the total measurement
  budget. They are not accepted paired comparisons.
- The unmodified heading/section query timed out before returning results at
  snapshots 95195 and 95976. At 95195 the materialized candidate completed in
  6.75 seconds (profiled repeat 0.76 seconds), but this proves no production
  equivalence to that unmodified query.
- Exact heading-first substring discovery timed out in the candidate itself
  after 120 seconds at snapshot 95327. No prose substitute was attempted.

## Decision

Activate only the exact-URL capture/heading family in the experimental API.
Keep materialized section inputs research-only until a complete comparison with
unmodified SQL and an end-to-end service check pass. Do not activate heading-first
discovery. No global native optimizer setting, persistent materialization,
partition policy or shared catalogue definition changes are included.

The activation requires exactly `capture` and `html_heading`, connected by an
eligible plain inner content-ID join and a single exact capture `effective_url`
equality predicate, with string parameter bindings. Additional predicates and
join residuals remain unmodified in this initial activation. Existing conservative syntax/semantics guards still apply; LIKE,
outer joins, user limits, sections and arbitrary discovery remain unmodified.
Installed definitions are verified in the same transaction after binding the
original SQL. Stable retains compiler `public-query-v4:stable`; experimental
reports `public-query-v5:experimental` and `capture_heading_content_scope_v1`.

These experiments reduce work for a selective query shape. They do not prove
constant work as the corpus grows, cold-S3 behavior or a billion-content-ID access
path. Missing scan metrics in ordinary-execution runs are unknown, not zero.

Reproduction: use the shared production reader bench with
`--case exp-selected-headings --content-scope --warm-runs 0 --seconds 120`, then
repeat with `--candidate-first`. For an in-cluster reader add
`--access-path cluster_direct_reader`. Section research uses
`--case exp-selected-sections --content-scope --materialize-inputs`; discovery uses
`--case exp-heading-discovery --content-scope --heading-driver`.

Rollback: remove experimental activation and deploy a new immutable release;
there are no stored derived results or schema changes to reverse.

## Service validation

The actual experimental QueryService, including native original binding, definition
validation, candidate planning and the normal result wrapper, completed the
unchanged heading query in **7.38 seconds** with a 60-second limit at snapshot
96256. All three rows, columns/types and the ordered digest above matched; SQL
was preserved and the response was not truncated. The response identified
`public-query-v5:experimental` and `capture_heading_content_scope_v1`.

`make check` passed, including backend, SDK and frontend checks/builds. Targeted
service tests cover stable isolation, parameter preservation, incompatible
installed definitions and unsupported broad queries. Differential fixtures cover
duplicate captures, empty/full selections and section boundaries for research
strategies. Benchmark tests cover ordinary repeats, result changes and missing
profile metrics.
