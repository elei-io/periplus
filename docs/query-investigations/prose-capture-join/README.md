# Prose search followed by capture lookup

Investigation: 2026-09-11. Classification: compiler/optimizer behavior,
provisionally. The separate inputs complete quickly and are modest in size;
there is no evidence here that a word index is necessary to make this join work.

## Failing query

```sql
SELECT c.effective_url, left(p.text, 1500) AS preview
FROM public_v1.capture c
JOIN public_v1.prose p USING (content_id)
WHERE regexp_matches(lower(p.text),
  'acquisition criteria|seeking acquisitions|add-on acquisitions|buy-and-build');
```

Tests used a temporary read-only production reader, deployed image
`sha-63a6bc2`, two DuckDB threads, 256 MB spill budget, and a 4 GiB container
limit. Default DuckDB memory budget was 512 MB. No application deployment or
lake mutation was performed.

The capture count completed in 392 ms: 127,762 rows and 121,643 distinct content
IDs. The matching prose count completed in 2,569 ms: 1,398 matches, 18,193,641
full-text characters, and 2,091,363 preview characters. These preliminary
counts were separate from the later frozen comparison transactions.

## Working execution experiment

In one connection and read transaction, fully consume this bounded selection:

```sql
SELECT content_id, left(text, 1500) AS preview
FROM public_v1.prose
WHERE regexp_matches(lower(text),
  'acquisition criteria|seeking acquisitions|add-on acquisitions|buy-and-build');
```

Bind its two columns as equal-length arrays to the second statement:

```sql
SELECT c.effective_url, m.preview
FROM public_v1.capture c
JOIN (
  SELECT unnest($ids) AS content_id, unnest($previews) AS preview
) m USING (content_id);
```

This is a real execution boundary, not merely a materialized CTE. Preserve
duplicate matches and capture rows; do not deduplicate either input.

Measurements used the shared `benchmarks/query/` helpers for connection setup,
deadlines, bounded result consumption, and measurement. Total candidate times
include both statements and array preparation. Candidate and original ran in
the same read transaction for each pair:

| DuckDB memory | Snapshot | Candidate total | Original |
| --- | --- | --- | --- |
| 512 MB | 240617 | 3.179 s | OOM |
| 1 GB | 240648 | 3.045 s | OOM |
| 2 GB | 240656 | 3.024 s | OOM |

Every candidate returned 1,450 rows with two VARCHAR columns, without
truncation. Since the original failed, these pairs do not establish result
equivalence against a completed original execution.

A separate frozen transaction checked an independent reference: stream all
127,806 capture rows, map each matching content ID to its list of previews,
and emit all matching combinations in Python. Multiset equality, including
duplicates, passed for all 1,450 output rows. Three candidate repetitions in
that transaction took 3.096, 2.966, and 3.013 seconds. Snapshot ID was not
recorded for this reference run.

## Rejected explanations and limits

- Explicitly materializing both inputs as CTEs still failed at 512 MB.
- Forcing preview copying with string concatenation and substring still failed.
- Disabling `join_filter_pushdown` did not rescue the original query.
- The original failed pinning 256 KiB at 488.0 MiB used of a 488.2 MiB limit.
- Memory-tag sampling did not isolate the cause. Per-tag peaks occur at
  different times and must not be added to claim simultaneous peak usage.

The exact internal memory failure is unresolved. This establishes a working
execution strategy for this case, not a universal diagnosis or a released fix.
The 512 MB DuckDB budget test ran in a 4 GiB container; it does not demonstrate
operation under a 1 GiB process/container limit.

## Production implementation requirements

Implement a narrowly eligible content-first execution rule behind the query
API, preserving the original predicate, projections, null behavior, parameters,
and multiplicity. Enforce row and byte bounds on the intermediate result and
one shared admission slot, snapshot, and overall deadline across both phases.
Test overflow behavior and correctness before enabling it. Broader predicates,
large intermediate sets, and unrelated query shapes need separate evidence.

No persistent materialization or new index is part of this experiment.

## Implemented service validation

The branch implements `prose_matches_before_capture_v1` in experimental only,
compiler `public-query-v9`. It binds the original first, checks installed view
definitions and regex validity, and preserves the existing transaction, deadline,
admission and result limits. Native execution handles unsupported SQL or an
intermediate collection exceeding 100,000 rows / 8 MiB. Stable behavior is unchanged.

The shared staged benchmark accepts `--rule prose-matches` and the registered
`prose-capture-previews` case. `service_probe.py` separately exercises the actual
QueryService in a disposable reader using the normal reader environment. Run with
native stderr suppressed: storage exceptions can include credentials. Its first
experimental request builds a bounded independent Python equijoin inside the
service transaction; its timing is intentionally excluded. Subsequent timing
requests use ordinary service execution and independent snapshots. No result
values are printed.

With the implemented service, a 4 GiB reader, 512 MB DuckDB budget and two threads:

- The independent reference matched all 1,457 rows at snapshot 242007, from
  128,210 capture rows and 1,405 matching prose rows.
- Ordinary experimental requests completed in 5.153, 5.128 and 6.371 seconds,
  snapshots 242033, 242040 and 242057, without truncation.
- Stable failed with `OutOfMemoryException` under the same capacity settings.

### Container-memory acceptance gate

Both the combined reference check and a subsequent ordinary experimental request
were OOM-killed in fresh readers constrained to production's 1 GiB container limit.
The ordinary request did not construct the independent reference. Therefore this
is not merely benchmark overhead and the 4 GiB success is insufficient for release
into the existing container envelope.

Instrumented 4 GiB runs isolated the peak to prose selection, before the capture
join. One run reached 1,151,052 KiB process peak RSS. This differs from DuckDB's
managed memory budget. Removing the selector LIMIT, adding an ordering, or
splitting into two content-key ranges did not bring the measured peak below 1 GiB.
A one-thread diagnostic and 256 MB / 384 MB managed-memory diagnostics failed
with DuckDB OOM. These variants and settings are not part of the implementation.

No production resource setting or deployment has been changed. Activation needs
a proven process-memory envelope or further scan-memory work; do not infer that
an 8 MiB intermediate payload bounds native scan allocation.

A subsequent 2 GiB container test completed ordinary service requests in 6.109,
5.166 and 5.234 seconds, at snapshots 243896, 243914 and 243920. The first returned
1,485 rows and the next two 1,486 as ingestion continued; all were untruncated.
The independent reference request matched all 1,485 rows at snapshot 243871,
from 128,805 captures and 1,433 matching prose rows. Stable still failed with
DuckDB OOM in this same 2 GiB container / 512 MB managed-memory configuration.
This demonstrates a real implementation gain with a 2 GiB process envelope; it
does not establish safe operation at 1 GiB. The PR remains draft pending that
deployment-capacity decision or further memory reduction.

### Local checks

`make check` passed: 751 backend tests (34 environment-dependent skips), five SDK
tests, shared package checks/tests, and public/admin typechecks and builds. The
final service suite, including the added shared-benchmark regression, passed all
27 tests. Differential fixtures cover nulls, duplicate captures/prose, Unicode,
empty/full domains, aliases, parameters and join direction. Service tests cover
no discovery during prep/stable, definition mismatch, invalid regex recovery,
overflow, deadline/admission, and a writer commit between the two phases.

## Decision after increasing query capacity

The owner authorized a 4 GiB DuckDB budget for both query APIs. Homelab commit
`a0cd092c` deployed 4 GiB managed memory, 4 GiB Kubernetes memory requests and
6 GiB container limits for each fixed single query replica. Both deployments
became ready and Flux reported successful Helm release v45. This supersedes the
capacity decision pending above; it does not deploy this optimizer branch.

At the new limits, ordinary stable execution completes and is faster than this
candidate. A disposable production reader running actual QueryService code
measured both mode orders, each request with its own transaction:

| Order | Stable milliseconds | Candidate milliseconds |
| --- | --- | --- |
| Candidate then stable | 3444 | 4263, 4138, 4377 |
| Stable then candidate | 3448, 2308, 2350 | 5722, 4213, 4071 |

All returned 1,533 rows without truncation. These are observational timings on a
changing corpus, not a same-snapshot baseline/candidate equivalence claim. The
independent reference passed at snapshot 247904 with 130,408 captures and 1,481
matching prose rows. Timing snapshots span 247920–248117. The new capacity makes
stable viable without this rule; these runs do not establish an optimization gain
under the new deployment settings. Do not promote PR #57 on this evidence. Retain
the investigation for future constrained-memory work instead of adding execution
complexity to the current baseline.
