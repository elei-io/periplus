# Single-capture outbound links: production experiments

Status: implemented as an experimental pass; measured against the production lake.
No merge or deployment. 2026-09-14.
The checkout was rebased onto `origin/main` at `5e7c572` during this investigation;
framework edits, the business-case report and experiment files were preserved.
The deployed image is also `sha-5e7c572`.

## Question and acceptance

[Business case 10](../../QUERY_BUSINESS_CASES.md) asks for the top 50 destinations
from the latest retained IC3 capture. Its original SQL is preserved byte-for-byte
apart from surrounding whitespace in [the shared case](../../../benchmarks/query/cases/single-capture-links/query.sql).
Counts, complete result types and deterministic ordering must remain unchanged.

The user clarified that under five seconds is great, under ten seconds can be good
for demanding analytics, and scalability matters more than shaving milliseconds.
The case target is now five seconds. Earlier reports retain the initial one-second
target: their nonzero exits mean the target was missed, not a timeout or incomplete
execution. No measurements were replaced to hide those outcomes.

Classification: compiler/optimizer work for failure to push selected capture scope
through the public link membership check; physical layout/access path contributes
to fixed-ID file growth. There is no demonstrated need to change public relation
grain or remove the standalone link view's retained-capture semantics.

## Protocol and environment

Production reader comparisons use the shared `query_benchmark_production.py` and
`measure_pair`, serially, with a 60-second total deadline per variant. Each pair
pins one read transaction, compares all 50 ordered rows/columns/types by digest,
then records one separate warm analyzed execution. Baseline/candidate use the same
settings, native optimizers and snapshot. Useful candidates are checked in both
orders. Diagnostic dead ends are retained without claiming an accepted speedup.

DuckDB 1.5.5; two threads; effective memory 4 GiB; spill limit 90 GB (83.8 GiB as
reported). The operator wrapper imports production reader settings and credentials
only into the child environment. Production is read-only; the layout export writes
only a local ignored file. No production settings or data were changed.

The original and simplified direct-join forward pairs ran before the rebase; later
pairs used the updated checkout. They use the same engine and installed production
views. The bench hashes `public_v1` definitions; experimental SQL definitions were
also inspected from the deployed package, matching the rebased checkout. No complete
experimental catalogue hash was collected by this runner.

Snapshots changed between pairs (426669 onward); comparisons are valid within each
pair. Maintenance also reduced some file counts later. Do not pool first-execution
times across snapshots or call any run guaranteed cold. Direct-reader latency includes
the developer machine's metadata tunnel and object-store path, not the public API path.

## Production-reader results

Times below are warm **separate profiles**, not the earlier normal executions.

| Experiment | Snapshot | Original | Candidate | Link rows emitted | Link files | Complete equal result |
| --- | --- | --- | --- | --- | --- | --- |
| scalar-forward | 426669 | 5.311 s | 5.243 s | 30,850,037 | 8 | yes |
| direct-forward | 426669 | 5.112 s | 2.301 s | 59 | 6 | yes |
| direct-reverse | 426671 | 4.794 s | 2.096 s | 59 | 6 | yes |
| literal-reverse | 426669 | 5.404 s | 2.312 s | 59 | 6 | yes |
| membership-reverse | 426671 | 4.843 s | 4.613 s | 59 | 6 | yes |
| source-reverse | 426690 | 4.632 s | 2.428 s | 59 | 6 | yes |
| source-literal-reverse | 426690 | 5.127 s | 0.084 s | 59 | 6 | yes |
| source-literal-forward | 426690 | 5.751 s | 0.085 s | 59 | 6 | yes |

- `scalar.sql`: put capture selection in a scalar subquery. It still emits the whole
  link corpus: no useful improvement.
- `direct-join.sql`: keep the selected capture join, read link occurrences directly,
  and omit only the membership check already guaranteed by that join. It gives about
  2.2× warm improvement in both orders and emits 59 rows instead of 30,850,037.
  It does not justify deleting membership checks from arbitrary standalone link reads.
- `literal.sql`: supply the known capture UUID to the public link view. This confirms
  literal scope reaches the scan. The quoted time excludes selecting the ID.
- `membership-in.sql`: express the same view membership check using `IN` rather than
  correlated `EXISTS`. It preserves semantics but yields little warm improvement.
- `source-scope.sql`: add the capture's effective source URL as a second join key,
  keeping visit identity authoritative. Merely expressing this join still reads six
  link files; it does not prove the source restriction becomes a narrow scan predicate.
- `source-literal.sql`: both capture ID and normalized effective source URL supplied
  as literal scan predicates. Link lookup alone takes 83.7/84.9 ms warm in opposite
  run orders, versus paired originals of 5.13/5.75 seconds. Normal candidate executions
  were 757 ms when first and 156 ms when second. These exclude key resolution and
  cannot establish a 60× end-to-end production speedup. Both still report six link files.

The initial normal executions were roughly 12–35 seconds on the developer reader,
while subsequent profiles were 2–5 seconds. These are different executions and cache
conditions. Do not advertise the first-to-second timing ratio as an optimization gain.

The profiler's `rows_scanned` counter remains 61,700,074 for many scans of a 30,850,037-row
relation, including selective scans. Do not interpret it as exact physical rows examined.
Likewise 59 emitted rows does not prove bounded file I/O. Candidate-first profiles report
about 100–140 MB peak buffer memory versus roughly 7.7–8.0 GB for original executions.
These are profiler counters, not pod RSS; same-connection peaks may carry earlier work.
They support investigating memory amplification, not a precise per-query memory saving.

## Implemented pass: complete unchanged-query comparison

`capture_link_scope` is registered as an experimental alternative in compiler v17.
It checks the installed contract, resolves bounded capture/source keys, reuses the
selected IDs with their original multiplicity, and supplies both literal predicates
to the private link scan. The user submits the original case SQL unchanged.
Selection and execution share the service's snapshot, deadline and connection.
Limits are 128 captures, 8 KiB per source URL and 128 KiB total keys. Unsupported
shapes, contract mismatches and exceeded budgets decline without truncating answers.

The final registered-pass measurements below use **ordinary warm executions**, not
profiles, and include contract checks, key lookup, rewriting and result retrieval.
Both pairs use snapshot 426708 and the same engine/settings described above. Each
contains one normal and one warm execution per variant; these are paired observations,
not a latency distribution. All complete ordered results, columns and types match.

| Order | Original warm | Complete pass warm | Of which lookup/check/rewrite | Warm improvement |
| --- | --- | --- | --- | --- |
| Original first | 5.160 s | 2.516 s | 2.428 s | 2.05× |
| Candidate first | 5.542 s | 2.403 s | 2.320 s | 2.31× |

Normal executions were original/candidate 30.882/2.458 seconds in original-first
order and 21.511/12.410 seconds in candidate-first order. Startup/cache effects are
substantial: do not promise every request finishes under five seconds. The remaining
82–88 ms after key lookup is consistent with the isolated two-key scan experiment.
No profile or process-RSS counters were collected for these ordinary executions.

An initial implementation repeated capture selection in the final SQL. It preserved
answers but measured 4.38–4.48 seconds warm versus 4.90–5.67 seconds for the originals.
Reusing the selection within the same snapshot removes that duplicate work. The
reported final pass includes this change; the earlier outcomes remain in local reports.

Real DuckDB differential tests cover redirects, normalization, null effective URLs,
empty and multiple captures, duplicated capture membership, aliases, both schemas,
unsupported shapes, budgets and installed-contract mismatches. A real DuckLake service
test verifies unchanged SQL activation, lookup inside the reported source snapshot,
preparation without reads and streaming row limits. Deployed-service latency and
production RSS remain post-deployment acceptance checks.

## Actual public API observation

A serial SDK lookup of the capture ID followed by the literal public-link query used
snapshot 426671 for both responses:

| Step | API time | Client time |
| --- | --- | --- |
| Select capture ID | 504 ms | 717 ms |
| Retrieve links for that ID | 282 ms | 339 ms |
| Combined | 787 ms | 1,056 ms |

The ID matched the frozen diagnostic UUID and the complete 50-row answer, columns and
types matched the original business-report result. These remain two separate transactions;
matching snapshot IDs were observed, not enforced by a shared SDK transaction.
This is one observed workflow, not a deployed optimizer result or a p95 estimate.
The original report's 3.196-second API execution is historical, not a paired API baseline.
The heavy original was not replayed in the live pod after the reader showed high memory.

## Layout and growth experiment

Exported all 30,850,037 rows at snapshot 426671, keeping visit ID, source URL, target URL,
observation time and occurrence ID. Other columns were intentionally excluded; this is
an isolated physical access experiment, not an end-to-end production clone.

Both layouts contain identical rows. Verify counts and a summed row hash at each scale,
and require exact ordered lookup-result digests across all sizes/layouts. Hashes are
regression evidence, not a formal multiset proof; construction changes only ordering.
The selected capture and its 59 link occurrences are always retained. Unrelated captures
are admitted by a deterministic hash at approximately 10%, 50% and 100% of the exported
corpus. This is unrelated-corpus growth, not growth of selected scope.

[The experiment script](growth.py) uses the shared bench measurement/deadline primitives,
local DuckLake tables registered from ZSTD Parquet, 122,880-row groups, eight row groups
per file, and the same two-thread/4-GiB settings. Both layouts use identical settings
and one snapshot per pair; fresh DuckDB connections are used in reverse-order pairs.
Operating-system caches remain uncontrolled. Builds/checks finish before timed queries.

| Link rows | Files in either layout | Files read: source-URL sort, capture-ID predicate | Files read: capture-ID sort, same predicate |
| --- | --- | --- | --- |
| 3,045,460 | 4 | 3 | 1 |
| 15,447,675 | 16 | 16 | 1 |
| 30,850,037 | 31 | 31 | 1 |

Both run orders agree. Warm local profiles grew from approximately 5.4 to 9–10 ms
for source sorting; capture sorting stayed around 5.2–5.6 ms. These local SSD timings
must not be projected onto remote storage. The stronger evidence is 3→16→31 files
versus one file for the same selected capture.

A preliminary one-file-per-layout comparison read one file in either layout and showed
no warm speed advantage (roughly 8–10 ms versus 10 ms). UUID row-group statistics made
251/252 groups eligible under source sorting versus 1/252 under capture sorting, but
eligibility is not a measured scan count. That experiment alone did not establish scaling;
the multi-file growth experiment above addresses that missing dimension.

At full size, capture sorting costs about 23% more compressed bytes (676.7 MB versus
551.4 MB for these five columns). A neighboring exact source-URL query reverses the
file-read advantage: one file under source sorting, 31 under capture sorting. Local
warm time is about 3.7 ms versus 5.2 ms. A blanket sort replacement trades one access
path for another and needs broader workload evidence before any production rebuild.

Providing **both literal capture ID and literal source URL** reads one file at every
scale in the existing source-sorted layout, in both orders, while preserving the answer.
This suggests exploiting the existing sort key may achieve the desired scaling without
rebuilding storage. It requires an explicit bounded key-resolution step; the ordinary
source-key join above did not achieve that automatically.

## Recommendation and production expectation

The implemented bounded capture-link scoping pass is an explicit alternative in the
existing compiler registry. It recognizes the supported capture/link inner join,
resolves selected capture IDs and normalized effective source URLs within the request's
snapshot/deadline, then constructs a narrowly filtered link read while preserving original
join multiplicities, ordering and predicates. ID membership remains authoritative. The IC3
case itself redirects from the requested HTTP URL to an HTTPS effective URL: the source
key must not be guessed from the requested page URL.

The implementation uses explicit small candidate budgets and declines unsupported or
oversized selections without truncating answers. Its tests cover redirects, null effective
URL fallback, normalization, empty selections, duplicate captures and multiple selected
keys; installed catalogue assumptions are checked before lookup. No new public column,
persistent index, queue, connection or general optimizer framework is indicated.

The simpler redundant-check removal is a useful intermediate result: approximately
2.2× warm improvement, already within the user's preferred five-second range. The two-key
lookup adds a stronger selective access path on existing storage. Avoid changing the
standalone link view merely to encode this one selected-join optimization.

Production expectation: improved completion and memory behavior are plausible, and a
sub-five-second full request is a reasonable acceptance target at today's corpus. The
observed two-request API workflow finished in 1.06 seconds including network time;
the 84–85 ms direct-reader lookup shows additional headroom. The implemented full pass now shows 2.05–2.31× warm improvement on the direct reader,
including key lookup, with unchanged-query activation covered by service integration
tests. It has not been deployed. Do not promise a p95, a fixed 60× improvement, or a
specific future latency before measuring the deployed service.

Scaling evidence: capture-ID-only access can read increasing unrelated files; adding the
existing source sort key holds file reads at one across this controlled 3M→31M ladder.
However, the experiment globally sorts fixed-size files, whereas live append batches and
maintenance can leave overlapping source ranges. Production still reports six files for
the two-key lookup. This is **not** proof of constant production file I/O as the corpus
grows, nor does it bound capture selection or historical growth for the selected URL.
Before closing a scaling claim, repeat with realistic append/compaction layouts and fixed
selected captures, and measure both key-selection cost and link extraction. Investigate
maintenance/layout only if that evidence still shows unrelated growth after scoping.

Do not rebuild storage now: capture sorting increases compressed size and weakens source
lookups, while existing source sorting can support the selected capture through a bounded
lookup. Retain the growth case as a physical-work acceptance check, not just a stopwatch.

Both query pods were healthy at the beginning and end; restart counts were unchanged.
The experimental pod's last termination was `OOMKilled` at 06:31:26 UTC, matching the
business report's outage sequence, but this investigation does not establish which query
caused that earlier OOM. No new query-pod restart occurred during these experiments.

## Reproduction and remaining work

Run the implemented pass through the documented homelab wrapper with:
`--case single-capture-links --optimization capture_link_scope --ordinary-warm-runs --warm-runs 1 --seconds 60 --report <ignored report.json>`.
Repeat with `--candidate-first`. This includes key resolution in measured time.

Run a research pair through the same wrapper with:
`--case single-capture-links --sql-override <absolute candidate.sql> --warm-runs 1 --seconds 60 --report <ignored report.json>`.
Repeat with `--candidate-first`; use unchanged settings and preserve all outcomes.

The disposable source export is one bounded local COPY of the five columns above from
`material.link_occurrences`; no production relation is written. Given that local input,
run from the repo root with the existing package Python:

```sh
packages/periplus/.venv/bin/python docs/query-investigations/single-capture-links/growth.py build \
  --input /absolute/path/links.parquet --output /absolute/path/new-disposable-lake \
  --source-snapshot 426671
packages/periplus/.venv/bin/python docs/query-investigations/single-capture-links/growth.py measure \
  --input /absolute/path/links.parquet --output /absolute/path/new-disposable-lake \
  --source-snapshot 426671
```

Repeat `measure` with `--source-url https://www.ic3.gov/` to exercise both literal
keys on the same growth ladder. This known-key diagnostic excludes capture selection.

Raw reports and private supporting scripts remain ignored under
`.artifacts/query-benchmarks/single-capture-links/`. Keep only sanitized summaries in Git;
remove disposable exports when the investigation closes.

Validation: targeted semantic, service and benchmark tests pass. Research SQL comparisons,
the registered-pass pairs and the local growth script completed with matching answers.
`make check` passed after implementation: 795 core tests (35 skipped), 24 SDK tests
(6 skipped), package checks/tests/builds, and public/admin checks/tests/builds.
