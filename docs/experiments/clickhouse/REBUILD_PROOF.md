# Rebuild protocol proof

2026-09-15. Reviewed design: [REBUILD_DESIGN.md](REBUILD_DESIGN.md).

## Decision

**Proceed with implementing this design.** A reproducible experiment against real
local ClickHouse, Postgres and FILE-backed JetStream supports its central coverage,
resume and publication mechanisms. It also reproduces the failure of the simpler
"historical scan plus new events only" approach.

This is confidence in the protocol and its implementation direction. It is not a
claim that the deployed Periplus workers already support rebuilds, that the complete
retention protocol is implemented, or that homelab throughput has been established.

## Reproduce

From the experiment worktree, with its local Compose services running:

```sh
uv run --project packages/periplus python scripts/rebuild_proof/proof.py \
  --run --output .artifacts/clickhouse/rebuild-proof.json

PERIPLUS_TEST_CLICKHOUSE=1 uv run --project packages/periplus python \
  -m unittest discover -s packages/periplus/tests \
  -p 'test_clickhouse_rebuild_proof_integration.py'

make check
```

The proof rejects non-local service endpoints. It creates randomly named ClickHouse
and Postgres namespaces, a restricted temporary query user, temporary raw files and
its own NATS streams/consumers. Cleanup runs in finally, including after assertions.
It does not stop application services, crawl websites, or write application tables.
Generated results and check logs belong under ignored `.artifacts/clickhouse/`.

Validation: the opt-in integration test passed; the full `make check` passed
(784 backend tests, 45 skipped; 24 SDK tests, six skipped; shared package checks/tests
and public/admin checks/builds). The opt-in proof is skipped by the ordinary run
and was executed separately against the real stores. The final harness also passed
directly with machine-readable output after the additional concurrent-work checks.

The implementation is deliberately an experimental harness in
`scripts/rebuild_proof/proof.py`. It reuses Periplus's actual HTML parser, canonical
HTML projection, link resolver, SQL validator, raw FileObjectStore and ClickHouse
client. The target/range/publication adapters are small prototypes, not production
repositories. Source fixtures use bounded integer scan keys and a reduced immutable
input envelope; they do not claim to benchmark the production visit table layout.

## Evidence

### Historical coverage, live catch-up and a real negative control

Six visits use four distinct raw HTML documents:

| Visit | Timing / initial state |
| --- | --- |
| 10 | Both original consumers ACKed it; removed from the stream before rebuild |
| 20 | Pending only on ingestion; base evidence absent when history is scanned |
| 30 | Pending only on old materialization; base evidence present |
| 40 | Pending on both original consumers |
| 50 | Published after the replacement consumer is created |
| 5 | Published after creation; its eventual base row sorts behind the completed scan position |

The historical scanner completes keys 10 and 30 before the delayed base inserts.
The replacement `DeliverAll` consumer receives 20, 30, 40, 50 and 5. Final durable
source and target IDs both equal `[5,10,20,30,40,50]`, with one row per visit.
Complete target rows and content payloads equal a separate clean offline run.
Expected visit-relative link URLs are checked independently against the fixtures.
Conflicting replay of an existing visit is rejected.

A second real consumer using `DeliverNew` receives only new events. Combining it
with the same historical scan **misses visits 20 and 40**. This is an observed
counterexample, not merely a hypothetical warning.

### Crash-safe publication and range progress

Three separate worker processes consume the same real durable range message and
exit abruptly at injected failpoints:

| Crash point | Process exit | Durable range cursor | Required recovery |
| --- | ---: | ---: | --- |
| After content insert, before visit publication | 31 | 0 | Partial content remains hidden from the query views |
| After verified visit output, before checkpoint | 32 | 0 | Retry reconciles existing output without duplication |
| After Postgres checkpoint, before NATS ACK | 33 | 30 | Redelivery resumes beyond the committed page |

The parent subsequently receives the message with at least four deliveries, finishes
the range and ACKs it. Candidate parsing occurs exactly four times for four distinct
contents despite the crashes, overlap and repeated visits.

These are worker deaths, not simulated exceptions around an in-memory checkpoint.
They do not constitute a ClickHouse/Postgres/NATS server power-loss or restore test.

### Completion cannot jump over a failure

The raw object for visit 40 is temporarily unavailable. Its delivery stays
unacknowledged while later work and the marker are ACKed. The marker sequence is 7;
the candidate's contiguous ACK floor remains 3. The readiness function rejects the
build with `catchup_incomplete`, and its phase remains building.

After repairing the input and verifying its output, both ingestion and candidate
floors reach 7 and readiness succeeds. Candidate output for visit 20 is also checked
to remain invisible before its matching base evidence arrives.

Deleting and recreating the candidate durable under the same name produces a new
creation identity. The readiness function rejects it with
`consumer_incarnation_changed`, even though the name still matches. Source protection
remains held. Rebinding an existing consumer without recreation preserves its identity.
This tests consumer replacement, not a complete stream backup/restore incarnation policy.

### Query publication under concurrent work

Two query clients execute **120 joins across capture, content and link views** while
the control publication switches **40 times** between compatible old/new targets.
Every statement captures a target before execution. Each result contains a consistent
revision across all three relations; there are **zero mixed-version results**.
Both versions are observed by both readers.

During that interval the harness also writes 256 background visit outputs and sends
two fresh visits through the real event stream, base writer and both material targets.
The original six visits remain present in query results while the new visits appear.
This is a small overlap test, not a sustained overload or freshness percentile test.

Additional checks cover unqualified columns, table-qualified columns, fully qualified
columns, CTEs, a CTE sharing a public relation's name, and UNION. An already-bound
old query continues to return the old revision after publication changes. A stale
Postgres publication compare-and-swap updates zero rows. The temporary read-only
query account receives ClickHouse permission error 497 when reading the private
source table, while its approved definer views work.

The test exercises the production SQL validator followed by the prototype binding
operation and actual ClickHouse execution. It does **not** yet exercise HTTP manifest
refresh, multi-process admission fencing or the deployed QueryService lifecycle.
The prototype reads publication control directly; production will use the approved
control API and immutable cached binding described in the design.

### Cancellation and safe progress

A worker writes its current output, then is held before committing its range
checkpoint. The controller changes the run to cancelling and advances its revision.
While the worker is still in flight, the source protection remains set and raw bytes
remain present. After draining, the worker's stale checkpoint is rejected; the last
verified cursor stays 50. Only then does the experiment retire the consumer and
release its build protection.

This verifies the checkpoint fence and the required drain sequence. It is **not**
a proof of distributed raw-reader exclusion or the janitor's eventual deletion
protocol. The experiment does not delete shared application raw content.

### Batching and content reuse

A separate small fixture materializes 512 visits sharing two short HTML documents,
in pages of 64. It requires **two parses and nine insert requests**: one content
batch and eight visit batches. One local run took roughly 0.13 seconds for this
section. Raw payloads are tiny, the services are local, and the keys are synthetic;
do not extrapolate a homelab throughput or corpus-size limit from that timing.

The useful evidence is structural: parsing tracks distinct missing content, and
insert requests track batches rather than visits.

## Design refinements established by the proof

1. Preserve `DeliverAll` and consumer identity; never reset an existing build's
   consumer to recover from ambiguous setup or lost state.
2. Make the barrier a named, persisted verification boundary. Early marker ACK,
   total counts and absence of pending work are not substitutes for completion.
3. Advance checkpoints after verified output and fence them on cancellation. A
   checkpoint fence alone does not stop a remote write: draining remains necessary.
4. Bind every reference in a query to one immutable publication. The binding must
   preserve aliases and qualified column references; replacing table-name strings
   is insufficient. Explicit view output column names also avoid accidental aliases.
5. Move missing-output checks before parsing and batch the storage operations.
6. Keep exact input/output reconciliation and protection ownership as production
   invariants. The experiment does not replace the existing write-claim protocol.

## Confidence boundary and next implementation gate

The experiment establishes that the proposed mechanisms work together on a small
adversarial fixture using the actual local stores and real process death. It is
sufficient to implement the narrow production rebuild path without introducing a
new general workflow framework.

Before calling that path production-ready, port these cases to the actual worker,
repository, query-service and operator APIs, then add:

- concurrent target writers under real identity claims, ambiguous write responses
  and stale worker ownership; existing E2E tests cover some underlying write behavior,
  but not the new target-aware composition;
- permanent-failure/dead-letter accounting through the completion barrier, beyond
  the unacknowledged missing-raw case tested here;
- actual partition-aligned scans, multiple ranges and out-of-order range completion;
- manifest distribution, stale query instances, server-side reader draining and
  rollback after live delivery has moved forward;
- stream loss/restore, coherent storage restore, replica visibility and server crashes;
- raw retention versus active rebuilds/readers, shared content and retirement replay;
- sustained representative ingestion, query and rebuild load on homelab, including
  merge pressure, large documents, backpressure and cancellation under low disk space.

No numerical homelab acceptance threshold has been inferred from this proof. The
first production implementation should preserve these tested invariants and replace
the prototype adapters, not copy the whole harness into runtime code.
