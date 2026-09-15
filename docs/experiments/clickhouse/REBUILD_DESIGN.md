# Rebuild design: a second materialization target with bounded catch-up

Design review, 2026-09-15, against E2E checkpoint `b982c6a`.
This refines the rebuild proposal in PIPELINE_PLAN and DESIGN_REVIEW. It is a
recommended implementation contract, not a claim that rebuilds are implemented.
The subsequent [protocol proof](REBUILD_PROOF.md) exercises its core mechanisms
against local ClickHouse, Postgres and JetStream and lists remaining production gates.

## Decision

A rebuild runs the ordinary deterministic materializer against a new private
output target. Historical ranges supply the backlog; one replacement JetStream
consumer supplies retained and future events. Queries continue using the previous
target until the replacement passes an explicit completeness gate.

Keep three small kinds of control state: **build, scan range, publication**.
Keep corpus-sized evidence and outputs in ClickHouse, pending delivery in NATS,
and bytes in the raw repository. Do not create a Postgres row per document or a
second permanent success ledger. Do not introduce a general DAG/workflow engine.

Start with one candidate build at a time and the HTML/link family. This is an
operational capacity limit, not an assumption that the corpus will remain small.

## 1. Separate material semantics from the rebuild operation

A family owns its concrete output schema, deterministic transform, input needs,
explicit semantic version, and completeness check. The initial HTML family owns
content structure and visit-specific link resolution. Keep dependencies explicit;
rebuild only the changed family and affected dependents.

A **target** is a private set of physical tables for a fixed family version and
its pinned dependency targets. Rows continue arriving into it normally. Its table
bindings and transform version do not change midway through a build.
A **publication** is a tiny immutable mapping of public relations to compatible
targets. It does not copy data and does not imply a frozen corpus snapshot.

A new target is not needed for every deployment. Logging/retry changes need no
rebuild. A native index change may require physical index work but no HTML parse.
A parser change requires the affected content semantics to be recomputed.

Use separate target tables initially. This makes incompatible schemas, inspection
and whole-target cleanup explicit; do not mix abandoned versions indefinitely in
one table behind a row-by-row "current version" lookup. Retain at most the active,
candidate and deliberately protected previous targets, with unchanged families shared.
Public users keep `public_v*`; target IDs remain private.

## 2. The same materializer for historical and live inputs

The shared path should be readable as:

```text
load bounded input metadata
  -> batch-check complete output
  -> claim and recheck missing content
  -> read and parse only missing content
  -> derive visit-specific output
  -> batch-write and verify publication
  -> advance source checkpoint / ACK
```

The current `materialization/storage.py:59` parses before `publish()` checks for
existing output. Fix this before using it for rebuilds. Repeated visits and retries
must reuse completed content. Store the content-level information needed to resolve
links against each visit's URL, so reuse does not require parsing the bytes again.
Different visits still retain distinct URL resolution and provenance.

Use target-scoped output identities, full input/output digests and bounded exact
write claims. Different targets must not serialize unnecessarily on an old global
materialization identity; retention exclusion still needs the shared source identity.
The transform is immutable for the build. Changed code that changes output requires
a new semantic target, not "retry until the conflicting output is overwritten".

Batch input lookups and inserts by bytes/rows/time. Preserve complete-publication
checks across partially successful writes. Do not turn a corpus rebuild into millions
of one-row HTTP inserts and metadata lookups. ClickHouse documents the part pressure
from tiny synchronous batches; native async batching with acknowledged flush is an
option to measure, not a replacement for exact replay/publication semantics.
[Insert batching](https://clickhouse.com/docs/concepts/features/operations/insert/asyncinserts)

## 3. Small, explicit durable state

Proposed logical records in control Postgres; names are illustrative:

| Record | Necessary fields / purpose |
| --- | --- |
| Build | ID, family/semantic version, pinned input dependencies, exact target tables, selection, live consumer, stream incarnation, phase, requested action, source protection, verification barrier, blocker |
| Scan range | Build ID, stable bounds, last completely verified key, claim/revision, processed counts/bytes, error and timestamps |
| Publication | Immutable relation-to-target mapping; one selected publication revision for each public API version |

Build phases: `preparing → building → verifying → ready → serving → retired`.
Cancellation is an explicit path through stopping/draining to `cancelled`.
Pause/backpressure and blockers are observable conditions, not dozens of extra
success states. An interrupted operation resumes from durable state.

The range checkpoint advances only after every selected input in that page has
verified complete output (or an explicitly modeled not-applicable result).
A crash before checkpoint commit repeats one bounded page. A crash after it can
safely move on. Counts are diagnostic; they are not the proof of completeness.

Use the existing materializer fleet with one low-priority backfill subject carrying
range descriptors, not document lists or parse results. JetStream owns delivery.
Workers checkpoint ranges; a small controller reconciles desired unfinished ranges
and target/consumer existence. Duplicate notifications are harmless. Publish only a
bounded number ahead, and re-notify unfinished work after uncertain publication;
no permanent per-batch publication/receipt ledger is needed. Range ownership and
checkpoint fencing must prevent a stale worker from advancing or retiring a range.

Keep this controller in the existing materializer process under one scoped operation
owner. No new service, nested leadership election, or Postgres transaction over
remote I/O. The controller chooses work; correctness lives in immutable targets,
verified commits and conditional state transitions.

## 4. Historical scanning without a corpus snapshot or billion-ID plan

Before any scan, establish the build's retirement protection and confirm the
replacement durable consumer exists with the intended filters and `DeliverAll`.
If creation succeeds but the response is lost, inspect the same consumer and resume;
never delete/recreate it to "repair" progress.

Then enumerate finite logical source ranges and scan ordered keyset pages. The
current visit table is partitioned by finish month and ordered by
`(requested_url, finished_at, visit_id)`. Start with ranges aligned to that actual
layout. Do not paginate a different key without proving a useful physical access
path. Avoid OFFSET, lists of every visit ID, physical part names as durable cursors,
or hashing each row into a bucket if that forces repeated full scans.

Fix range upper bounds for this run. New rows and even new historical partitions
may appear after planning; the live consumer covers them. Keep reads authoritative,
source rows immutable and protected from deletion. Additional replicas cannot be
used for absence/coverage proofs until their visibility guarantees are established.
The initial proof is for the current single authoritative ClickHouse route.

The coverage argument is short:

| Event at consumer creation | Why the replacement sees it |
| --- | --- |
| Already removed after successful ingestion/materialization ACKs | Its accepted base evidence exists before scanning and survives under protection |
| Still pending on either original consumer | `DeliverAll` supplies the retained event |
| Published afterward | The replacement consumer receives it live |

A delayed commit behind a completed keyset page therefore arrives through the
replacement consumer. A maximum UUID or ingestion timestamp alone cannot provide
this property. Parked failures, administrative purges and restore events are not
silently covered by this argument: reconcile them explicitly or invalidate the run.
Consumer delivery behavior is documented by NATS.
[Delivery policies](https://raw.githubusercontent.com/nats-io/nats.docs/master/nats-concepts/jetstream/consumers.md)

This depends on stream retention preserving the candidate's interest. Keep pending
work free of automatic age expiry and fail publication visibly at the byte limit.
A candidate that falls behind can fill the shared stream; this is a capacity and
lifecycle concern, not something an activation flag can fix.
[Interest retention](https://docs.nats.io/learn/jetstream/retention-policies)

## 5. Catch-up means passing a named barrier, not observing an empty queue

After the historical ranges finish, publish a small typed rebuild barrier that
both the ingestion and replacement consumers receive. It carries only build identity;
its acknowledged stream sequence is the boundary B. Give this control message an
explicit schema/filter contract; it is not fake visit evidence and creates no visit row.

The simplest initial rule is strict: the relevant consumers must have successfully
completed all prior required work through B. An early ACK of the marker itself is
insufficient; check the contiguous acknowledgment floor. Also reconcile known failures:
TERM/dead-letter operations are not successful material publication. Unresolved
required failures block activation, with identity and reason visible. Do not skip a
bad document merely to make progress green. An accepted not-applicable result is a
different, typed semantic outcome.

Consumer ACKs establish this boundary only because the worker contract requires
verified durable writes before ACK. They do not independently prove database
visibility. Historical checkpoint verification, the live completion contract,
query-route checks and failure reconciliation together establish readiness.

B bounds a completed prefix, not all events forever. Events after B keep arriving.
Check live freshness again before activation, and retain the replacement consumer
as the serving target's consumer. Never delete it and start a new consumer at "now".

A disposable local FILE/Interest-stream probe on 2026-09-15 observed:

```json
{"candidate_received":["2","3","barrier"],"barrier_sequence":4,
 "floor_after_early_barrier_ack":0,"floor_after_all_prior_acks":4}
```

Event 1 had been ACKed by both original consumers; 2 was pending only on ingestion;
3 only on the old materializer. The candidate received 2 and 3. The temporary stream
was deleted in finally; application streams were untouched. This verifies the local
NATS primitives only, not the complete rebuild protocol or replicated storage.

## 6. Publish by binding each query once

Do not sequentially replace several public views and call that atomic activation.
ClickHouse's `EXCHANGE` is atomic for one pair; multiple pairs are sequential.
[EXCHANGE guarantees](https://clickhouse.com/docs/reference/statements/exchange)

Prepare an immutable internal query-view namespace for the publication. Every view
in it names concrete compatible targets, including internal view-to-view references.
At request admission the query service captures one publication binding. Its existing
SQL table-resolution boundary maps permitted `public_v*` references to that namespace
once, before execution. This is one typed binding operation, not a general optimizer
rewrite. Keep the restricted database account limited to approved query views.

Control Postgres selects a fully validated publication with compare-and-swap on the
previous revision. Query services load the immutable manifest through the control API,
cache it outside the request hot path, and switch the local binding between requests.
A process starts ready only after loading and validating its binding. Stale instances
may serve the still-live previous publication during rollout; one query must never
mix versions. API readiness reporting must use the same publication binding as the
query route, rather than an independently switched family pointer.

Track loaded publication revision in existing service presence/health. Retire old
writers and grants only after query instances switch or are fenced from admission,
old requests drain and the rollback protection expires. Keep uncertain/dead instance
ownership protected until its bounded reader lease/timeout is established. Existing
server-side query limits help bound draining but do not justify dropping tables under
an unverified reader. If bindings cannot be observed/fenced, retain the old target.

This gives per-query **version consistency**, not a global data snapshot or an
instantaneous change across all clients. If instantaneous fleet-wide cutover becomes
a product requirement, use a short explicit admission pause and drain. Do not build
that distributed requirement into ordinary rebuilds by accident.

Rollback selects the previous publication only while its target is still current
and protected. After stopping its consumer, rollback requires verified catch-up;
otherwise call it restoring an older result set, not transparent rollback.

## 7. Performance and resource rules

- Reserve separate bounded live slots; only idle backfill capacity is expendable.
  Prioritize both serving and candidate live delivery over historical scanning.
- Bound raw reads, parser processes/memory, ClickHouse inserts and outstanding
  pages. A single oversized input must have a visible outcome, not an unbounded task.
- Run narrow sequential source scans; batch target lookups; parse once per missing
  content/semantic target; derive cheap visit output separately.
- Validate each page before advancing, rather than ending with an unbounded global
  anti-join. Sampled semantic queries complement that proof; counts/checksums alone
  do not prove missing-identity coverage. Prove the scheme with exact adversarial
  fixture comparisons before relying on it at scale.
- Set backfill budgets from measured query latency, merge pressure, live lag and
  disk headroom. Start with explicit operator-set limits and simple thresholds,
  rather than an elaborate adaptive scheduler.
- Provision disk for changed-family output plus old/candidate coexistence and merge
  headroom. Raw bytes and unaffected families remain shared. Target separation does
  not make a parser rewrite free: it still costs roughly the distinct input bytes
  needing parsing plus the output written.
- A long pause of the candidate also retains incoming events. Pause historical
  work first. Cancel a non-viable candidate through its drain/protection protocol
  before it exhausts the stream; recreating its consumer invalidates coverage and
  requires a new scan proof.

## 8. Make every stop explainable

One operator status should report family/version, phase, exact target/publication,
finished/remaining ranges, last durable key, verified rows/bytes, live pending age,
barrier B and each consumer floor, failure identity/reason, worker ownership, throughput
and free-space budget. Show estimates as estimates; never derive an exact percentage
from a changing corpus count.

Provide narrow actions: inspect, pause/resume historical work, retry a diagnosed
failure, cancel, validate, activate. A stale worker, missing raw object, output-digest
conflict, lost consumer or absent replica visibility should have distinct reasons.
Logs include build, target, range and input identity; metric labels do not contain
unbounded IDs. Inspecting one visit should connect base evidence, raw input, material
completion and any retained delivery/failure without reconstructing several job ledgers.

Cancellation first fences new work, drains bounded writes/readers, then retires the
candidate consumer, then releases source protections and marks exact target tables
eligible for janitor cleanup. Do not let the janitor infer ownership from table names.
A stream restore/recreation invalidates the recorded stream incarnation and boundary;
stop activation until re-establishing coverage. Coherent restore is a separate test.

## 9. Implementation and proof order

1. Extract the shared missing-output-first materializer and typed target identity;
   prove live/replay equality, batched publication and content reuse.
2. Implement one historical range with crash-safe checkpointing. Scale to a bounded
   set of ranges through one backfill delivery lane, preserving live capacity.
3. Add candidate consumer creation/recovery and the explicit barrier. Test delayed
   base commits behind scan positions, retained old events, new partitions, duplicate
   and out-of-order completion, parked failures and missing consumer state.
4. Add immutable query bindings and publication selection. Test a query joining
   several related views during repeated activation, lagging query-service refresh,
   long-running old queries, controller crashes and rollback.
5. Test cancellation/drain/protection and resource pressure. Prove sampled result
   equality against a clean offline build on a representative fixture, and exact
   identity coverage on an adversarial fixture. Measure larger corpora on homelab.

Acceptance: the operator can stop any worker and resume without guessing; no accepted
input escapes both scan and live coverage; no request mixes incompatible targets;
fresh captures remain within the measured freshness budget; and control-state growth
tracks ranges/builds rather than every content occurrence. Until these tests pass,
this is a reviewed design with a verified NATS primitive, not production-ready machinery.
