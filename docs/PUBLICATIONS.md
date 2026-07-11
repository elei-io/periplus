# Queries, Views, and Publications

Status: proposed

Atlas turns retained web evidence into tabular data that other analytical systems can consume.
This document defines the intended user-facing layers between catalogue exploration and durable,
incrementally consumable data. It describes product semantics and ownership rather than exact API,
database, or queue schemas.

## Motivation

The catalogue is useful for interactive SQL, but downstream data pipelines need more than an SQL
text box. They need stable table identities, typed schemas, bounded incremental reads, historical
backfills, corrections, and resumable consumption.

Atlas provides those source-side guarantees without becoming a destination connector platform or a
general data-pipeline orchestrator. External tools remain responsible for downstream transformations,
credentials, schedules, sink-specific merge behavior, and delivery into warehouses, object stores,
message buses, or other destinations.

## User-facing layers

Queries, views, and publications are separate concepts with deliberately different guarantees.

### Queries

A query is user-authored SQL stored by Atlas for exploration and reuse.

- Queries are editable definitions in the Postgres control plane.
- Every edit creates an immutable revision so previous SQL can be inspected and reproduced.
- A query revision may be run interactively, used to create a view, or assigned to a publication.
- Query history is an authoring history, not a materialized result history.
- Saving a query does not create a DuckLake table, maintain results, or promise incremental delivery.

Multiple query revisions may feed the same publication. A website can change its HTML structure
without changing the consumer-facing data contract. In that case, a new query revision applies from
a declared boundary while the publication retains the same table and schema.

### Views

A view is a real user-defined DuckLake SQL view. It is a named, non-materialized convenience layer
over catalogue tables, publications, or other views.

- DuckLake owns the view definition and is authoritative for its SQL and lifecycle.
- Atlas does not add query-style revision history to views.
- Replacing a view changes what its name resolves to; consumers that require a stable contract should
  use a publication instead.
- A view is evaluated when queried and is not an incremental delivery boundary.
- A view does not have materialization runs, backfills, corrections, or a CDC row stream of its own.
- DuckLake records view metadata and snapshot validity in its metadata catalogue. This implementation
  history does not make views versioned Atlas definitions.

Atlas may keep a Postgres control-plane reference to a DuckLake view when an active UI or API caller
needs Atlas metadata such as ownership, description, or discoverability. Such a record must reference
the DuckLake view identity and must not copy its SQL. DuckLake remains the single authority. Directly
created DuckLake views can be discovered or explicitly adopted rather than mirrored through dual
writes.

### Publications

A publication is a stable, typed DuckLake table defined by a user and managed by Atlas. It is the
consumer-facing dataset boundary.

- A publication has a stable table identity and declared row semantics.
- Atlas materializes and reconciles its rows from committed crawl evidence.
- One or more immutable query revisions describe how to derive those rows.
- The publication schema may evolve through explicit DuckLake DDL.
- Publication data carries provenance back to its crawl, document, query revision, and
  materialization run.
- DuckLake snapshots and CDC make publication changes incrementally consumable.

"Dataset" may be used in user-facing copy where it is clearer, but `publication` names the Atlas
concept: a dataset made available to systems beyond Atlas.

## Boundaries

Atlas owns:

- publication definitions, schemas, row identity, and change semantics;
- immutable query revisions and the rules that select them;
- live materialization from newly committed crawls;
- historical backfills and corrections;
- publication provenance and materialization status;
- safe schema mutation at the publication boundary.

Atlas does not own:

- destination connectors or destination credentials;
- downstream transformation graphs;
- consumer schedules;
- warehouse-specific merge or slowly changing dimension behavior;
- sink retries, quarantine, or exactly-once external side effects;
- a general workflow orchestration platform.

## State ownership

The existing Atlas ownership rules continue to apply.

| Owner | Publication-related state |
| --- | --- |
| Postgres control plane | Editable publication definitions, query revision metadata, applicability rules, declared identities, and optional references to DuckLake-owned views |
| NATS JetStream/KV | Queued materialization work, current run state, progress, retries, and worker presence |
| DuckLake | Authoritative views, typed publication tables, publication row history, DDL history, and crawl evidence |
| DuckLake CDC metadata | Downstream consumer subscriptions, leases, cursors, and audit state |
| Repository objects | Immutable raw HTML supporting publication provenance and later recomputation |

Prometheus and progress events remain observational and are never correctness state.

## Publication contract

A publication definition declares at least:

- a stable DuckLake schema and table identity;
- whether rows represent observations or current state;
- the columns that identify a logical row;
- a typed output schema;
- required evidence and materialization provenance;
- the query revisions eligible to produce rows;
- the rules used to select a revision for a crawl.

An observation publication normally includes the crawl identity in its effective key. A current-state
publication uses a declared business key and explicit ordering rules to decide which observation wins.
Atlas should default to preserving observations because they retain the temporal relationship to web
evidence. Current-state publications are an explicit projection with stronger reconciliation rules.

Exploratory output without stable row identity may be append-only, but Atlas must clearly report that
reliable correction, deletion, and upsert semantics are unavailable.

## Query revisions and applicability

Query revisions are immutable once activated. Changing SQL creates a new revision.

Revision selection may use:

- URL or site matching rules;
- crawl capture time;
- an explicit effective interval;
- priority when rules overlap.

Every materialization run freezes its crawl scope and the resolved query revisions before execution.
Changing applicability must not alter a run already in progress.

Applicability is based on the captured evidence, not the time at which a backfill happens. This lets a
historical crawl deterministically select the query revision that describes that era of the site.
Changing historical applicability is an explicit control-plane action that may require a correction
run; it is not a hidden side effect of editing SQL.

A query revision must produce the publication's identity and current output columns. A new query
revision does not require a new publication or table when the consumer-facing schema remains valid.

## Schema evolution

A publication may evolve through real DuckLake DDL instead of receiving a new table for every schema
change. Additions, removals, renames, and type changes create explicit schema boundaries.

CDC consumers must process the relevant DDL before accepting DML under the new shape. Whether a
particular mutation is safe depends on the downstream consumer, so Atlas exposes and validates the
boundary rather than hiding it behind a compatibility table or dual write.

Changes to row identity or observation/current-state semantics are more significant than ordinary
column DDL. Atlas must treat them as explicit contract changes and show their effect on existing rows,
backfills, and consumers before applying them.

## Materialization

All publication writes begin with evidence already committed to the repository:

```text
committed crawl
    |
resolve frozen query revision
    |
evaluate a bounded query for that crawl
    |
reconcile publication rows
    |
commit a DuckLake snapshot
    |
make the changes available to CDC consumers
```

The per-crawl evaluation is the primary incremental unit. A query may join `crawls`, `documents`, and
`elements`, but its live evaluation is bounded to the newly committed crawl rather than rescanning the
complete catalogue.

The repository worker remains the only DuckLake writer. Materialization may add stages around that
writer, but it must not introduce a second catalogue writer or bypass the durable ingestion boundary.
NATS continues to own queued work and current execution state.

Live materialization, backfills, and corrections use the same evaluation and reconciliation behavior.
They differ in how their crawl scope is selected and why the run was created.

## Backfills and corrections

A backfill evaluates a frozen historical crawl scope that has not yet been materialized for a
publication. A correction reevaluates evidence that may already have publication rows.

Each run records:

- publication identity;
- run kind: live, backfill, or correction;
- frozen crawl or time scope;
- frozen query revision selection;
- operator or triggering task;
- row counts and terminal outcome.

For each crawl and declared logical key, reconciliation produces the smallest truthful change:

- a newly derived row is inserted;
- a changed row is updated;
- a previously published row no longer derived is deleted;
- an unchanged row produces no write.

The query revision is provenance, not row identity. A correction can replace the revision that
produced a row without changing the logical row's identity.

Large backfills are explicit, bounded operations. They must not be hidden in interactive reads or
ordinary crawl requests. Their commits should remain small enough for bounded CDC consumption and
safe repository operation.

## Incremental consumption

Publication tables are ordinary typed DuckLake tables. Downstream tools may consume them as complete
snapshots or through DuckLake CDC.

The expected CDC contract is at least once:

```text
read bounded CDC window
    |
perform downstream transformations and writes
    |
commit the consumer cursor only after required sinks succeed
```

Consumers own sink idempotency and external side-effect semantics. Each consumer has an independent
cursor and can run continuously, periodically, or on demand. Atlas does not prescribe the pipeline
tool used to move publication data elsewhere.

NATS may reduce wake-up latency inside Atlas, but it is not the publication replay log. Durable
publication changes come from DuckLake snapshots, and durable downstream positions come from the CDC
consumer state.

## Replay and bootstrap

Three operations must remain distinct:

1. **Consumer replay** rereads retained publication changes by resetting or creating a CDC consumer.
   It does not mutate the publication table.
2. **Snapshot bootstrap** reads publication state at a consistent DuckLake snapshot and then starts
   CDC strictly after that snapshot. It is the recovery path when exact earlier change history is no
   longer retained or when a new consumer needs an initial copy.
3. **Rematerialization** asks Atlas to reevaluate source evidence. It may insert, update, or delete
   publication rows and therefore creates new, legitimate CDC events.

Re-running an external pipeline is a downstream operation and is not an Atlas rematerialization.

Exact CDC replay is limited by DuckLake snapshot retention. Atlas must expose retention and gap
conditions clearly; it must never silently substitute current state for missing historical events.

## Correctness invariants

- No publication row becomes visible before its supporting crawl evidence is durable.
- Materializing the same frozen input more than once is idempotent.
- Query revisions used by a run do not change while it executes.
- Publication row identity is independent of the query revision that produced it.
- A correction emits only the inserts, updates, and deletes required to reconcile derived state.
- DuckLake is authoritative for views and publication rows; Postgres references never mirror their
  SQL or data.
- Consumer notifications may be lost without losing publication data or replayability.
- A consumer advances its cursor only after its required downstream work succeeds.
- Missing retained CDC history is an explicit gap requiring replay reset or snapshot bootstrap.

## Initial delivery sequence

1. Add versioned saved queries and interactive revision history.
2. Add user-defined DuckLake views without a second SQL authority.
3. Promote a query revision into a typed publication definition.
4. Materialize newly committed crawls into an observation publication.
5. Add bounded backfill and correction runs using the same materializer.
6. Expose publication tables through snapshot reads and DuckLake CDC.
7. Add snapshot bootstrap, schema-boundary, retention, and consumer diagnostics.

## Open questions

- Which publication naming and DuckLake schema conventions should Atlas reserve?
- Should Atlas create a Postgres reference for every managed view or only views with Atlas-specific
  metadata?
- How should directly created DuckLake views be discovered and adopted?
- What is the first supported current-state ordering policy?
- How should live materialization interleave with a large historical backfill?
- Which schema mutations require an explicit impact acknowledgement?
- How should a consistent snapshot-to-CDC bootstrap be exposed to remote consumers?
- What snapshot retention defaults are appropriate for expected consumer lag?
- Should materialization ever use DuckLake CDC as an internal trigger, or remain entirely NATS-driven?
