# <issue>: <concrete workload>

- Status / owner / last reviewed:
- Production execution ID and SQL fingerprint (no private payload):
- User impact, time window, sample counts and priority:
- Workload family: known content / discovery / broad analytics
- Classification and evidence: schema / optimizer / both / unclassified
- Case directory and sanitized SQL / parameter selection:
- Required semantics, multiplicities, ordering and expected nonempty coverage:
- Acceptance criteria (latency, files, bytes, intermediate rows, memory):

## Baseline

Access path, snapshot, deployment/compiler/engine/catalogue versions, settings,
cache protocol, corpus and selected-key counts, file layout and maintenance state.
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

## Release and follow-up

Tests, unchanged public-query verification, deployment version, rollback plan,
post-deploy observation window and results, private-artifact cleanup, closure gates.
