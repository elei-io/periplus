# <issue>: <concrete workload>

- Status / owner / last reviewed:
- Production execution ID and SQL fingerprint (no private payload):
- User impact, time window, sample counts and priority:
- Original business case/report and unchanged acceptance query:
- Outcome / phase: completed, timeout, resource limit, admission, availability; execution attempted?
- Business usefulness / coverage (separate from execution success):
- Workload family: known content / discovery / broad analytics
- Classification and evidence: schema / optimizer / both / unclassified
- Case directory and sanitized SQL / parameter selection:
- Required semantics, multiplicities, ordering and expected nonempty coverage:
- Acceptance criteria (latency, files, bytes, intermediate rows, memory):
- Expected work for this family; relevant neighboring cases:

## Baseline

Access path, snapshot, deployment/compiler/engine/catalogue versions, settings,
cache protocol, corpus and selected-key counts, file layout and maintenance state.
For suspected pod OOMs: pod/container, termination reason and restart timestamps,
container limit versus DuckDB memory/spill settings, available process/container memory,
and correlation with query execution/concurrent work. Separate confirmed OOM termination
from suspected query attribution; record missing evidence explicitly.
Link private local artifacts only locally; commit sanitized summaries.

## Hypothesis and experiment

One cause, predicted measurable difference, controls, same-snapshot variants,
fixed-key versus scope-growth protocol, correctness fixtures and stopping budget.

## Results

| Variant | Snapshot | Complete? | Rows/equivalence | Wall time | Files/bytes | Peak memory/spill |
| --- | --- | --- | --- | --- | --- | --- |

Distinguish absent measurements, zero, and interrupted execution. Explain failures
and inconclusive results. Do not infer correctness from a timeout.

## Decision

Responsible layer, chosen intervention, rejected alternatives, wider workloads,
write/storage costs, upstream evidence, remaining uncertainty and next experiment.

## Implementation review

For code changes: module and explicit compiler registration; supported SQL; correctness argument;
lookup bounds and transaction/deadline ownership; applied/declined diagnostics; meaningful
tests; shared helpers justified by actual duplication; superseded paths to remove.
Keep this short and mark non-applicable items rather than creating extra machinery.

Acceptance: complete-answer correctness, workload improvement, resource/regression checks,
and readable code. Record any incomplete baseline without claiming equivalence or speedup.

## Release and follow-up

Tests, unchanged public-query verification, deployment version, rollback plan,
post-deploy observation window and results, private-artifact cleanup, closure gates.
For query-linked OOMs, include service survival/recovery and resource-containment evidence
alongside query completion within the agreed limits.
