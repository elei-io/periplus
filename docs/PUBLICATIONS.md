# Queries, Views, and Publications

Status: accepted design; implementation pending

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

Every view created or adopted through Atlas has a Postgres control-plane reference for ownership,
description, and discoverability. The record references the DuckLake view identity and never copies
its SQL. DuckLake remains the single authority. Directly created DuckLake views remain discoverable
and unowned until explicitly adopted.

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

## Names and catalogue layout

Atlas reserves three DuckLake schemas:

- `main` contains Atlas evidence tables and catalogue helpers;
- `views` contains persistent user-defined views;
- `published` contains Atlas-managed publication tables.

Physical view and publication names use lower-case snake case, match
`^[a-z][a-z0-9_]{0,62}$`, and are unique within their schema. Display names and descriptions are
separate Postgres metadata and may change freely. A publication's physical schema and table name
become immutable when it is activated because downstream consumers bind to that
identity. Renaming the product label does not rename the DuckLake table.

Atlas reserves the `_atlas_` column prefix in publication tables. User-defined output columns may
not use it. Every v1 publication table contains:

- `_atlas_crawl_id UUID NOT NULL`;
- `_atlas_document_id VARCHAR NOT NULL`;
- `_atlas_captured_at TIMESTAMPTZ NOT NULL`;
- `_atlas_query_revision_id UUID NOT NULL`;
- `_atlas_query_binding_id UUID NOT NULL`;
- `_atlas_materialization_run_id UUID NOT NULL`;
- `_atlas_materialized_at TIMESTAMPTZ NOT NULL`.

The publication's declared user identity columns plus `_atlas_crawl_id` form its effective logical
key. DuckLake constraints are not the correctness mechanism; the repository writer enforces this key
while reconciling a crawl.

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

## Control-plane definitions

The implementation uses explicit Postgres entities rather than JSON embedded in tasks:

- A **query** is the stable editable identity shown to a user.
- A **query revision** is an immutable SQL body, content hash, parameter declaration, creation time,
  and author. Updating a query inserts a revision and moves the query's current-revision pointer in
  one transaction. Revisions are never updated or deleted while referenced.
- A **catalogue view reference** stores the DuckLake `view_uuid`, current qualified name, display
  metadata, and adoption state. It never stores view SQL.
- A **publication** stores its stable UUID, immutable physical table identity after activation,
  display metadata, lifecycle state, observation semantics, and declared identity columns.
- A **publication field** gives every user column a stable UUID, physical name, DuckDB type,
  nullability, ordinal, and lifecycle state. The field UUID survives a column rename.
- A **publication query binding** selects one immutable query revision, maps its result columns to
  publication field UUIDs, and declares URL matching, half-open captured-time applicability
  `[effective_from, effective_until)`, and priority.

Bindings are immutable after activation. Changing matching, applicability, priority, or field mapping
creates a successor binding and archives the previous one for new resolution. Historical rows and
materialization records retain the binding UUID that actually ran.

Bindings may overlap only when selection remains deterministic. Atlas chooses the highest priority,
then the most specific URL match. Creation is rejected if two eligible bindings would tie. A crawl
must resolve to exactly one binding; zero matches is a visible skipped materialization, and multiple
top matches are a configuration failure rather than an arbitrary choice.

Definitions use archive state instead of destructive deletion once referenced by a run or durable
publication row. Draft, active, paused, and archived publication lifecycle states have these effects:

- `draft`: definition may change and no work is produced;
- `active`: new crawls resolve bindings and materialize;
- `paused`: existing data remains consumable but no new live work is produced;
- `archived`: no new work or schema changes are accepted; the DuckLake table remains readable until
  an explicit destructive repository operation removes it.

## Publication contract

A publication definition declares:

- a stable DuckLake schema and table identity;
- whether rows represent observations or current state;
- the columns that identify a logical row;
- a typed output schema;
- required evidence and materialization provenance;
- the query revisions eligible to produce rows;
- the rules used to select a revision for a crawl.

An observation publication normally includes the crawl identity in its effective key. A current-state
publication uses a declared business key and explicit ordering rules to decide which observation wins.
Version one supports observation publications only. They retain the temporal relationship to web
evidence and give backfills and corrections deterministic per-crawl behavior. Users can define a view
over observations to expose current state, normally by selecting the greatest
`(_atlas_captured_at, _atlas_crawl_id)` for each business key.

A future maintained current-state publication requires an active caller and a separate design for
late observations, winner deletion, and recovery of the previous winner. It is not an alternative
mode hidden in the v1 implementation.

Version-one publications require at least one declared user identity column. Exploratory output
without stable identity remains a saved query or view; it cannot be activated as a publication.

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

Saved queries may be arbitrary safe read-only SQL. A query revision becomes eligible for a
publication binding only when it satisfies the materialization contract:

- it is one read-only `SELECT` statement;
- it declares exactly one required named parameter, `$crawl_id`;
- it reads crawl evidence only through the bounded table macros `publication_crawl($crawl_id)`,
  `publication_document($crawl_id)`, and `publication_elements($crawl_id)`;
- direct reads of `crawls`, `documents`, `elements`, publication tables, arbitrary views, external
  files, attached databases, table functions, and network functions are rejected;
- approved scalar catalogue helpers such as `get_attribute`, `has_text`, `text_content`,
  `readable_text`, `inner_html`, and `resolve_url` remain available;
- its result column names are unique;
- its result-to-field mapping covers every non-null publication field;
- missing nullable fields are materialized as `NULL`;
- unmapped result columns are rejected;
- each result row has non-null declared identity values;
- duplicate logical keys in one crawl fail the crawl's publication materialization.

Atlas validates a binding by preparing the SQL, inspecting its Arrow schema, and exercising it
against representative retained evidence before activation. The frozen materialization plan contains
the query revision, parameter contract, field mapping, publication schema fingerprint, and binding
identity; the repository worker never resolves mutable definitions during execution.

Validation parses the statement and applies an allowlist; searching SQL text is not sufficient. The
three bounded table macros are installed and owned by the repository catalogue. Each returns rows
only for its supplied crawl identity, making total catalogue size irrelevant to one live evaluation.

## Schema evolution

A publication may evolve through real DuckLake DDL instead of receiving a new table for every schema
change. Additions, removals, renames, and type changes create explicit schema boundaries.

CDC consumers must process the relevant DDL before accepting DML under the new shape. Whether a
particular mutation is safe depends on the downstream consumer, so Atlas exposes and validates the
boundary rather than hiding it behind a compatibility table or dual write.

Changes to row identity or observation/current-state semantics are more significant than ordinary
column DDL. Version one does not permit changing publication identity columns after activation.
Create a new publication when the meaning of row identity changes.

All DDL for a managed publication goes through an Atlas schema-change operation. Direct out-of-band
DDL is detected by the publication schema fingerprint, pauses materialization, and requires explicit
adoption or repair; Atlas never silently changes its Postgres contract to match it.

The operation validates every active and historically backfillable query binding, previews the
DuckLake DDL and affected fields, and requires explicit confirmation. Version one supports:

- adding a nullable field;
- adding a non-null field with a deterministic constant default;
- renaming a field while retaining its stable field UUID;
- widening a field type when DuckDB can cast every existing value;
- dropping a non-identity field;
- changing nullability only after a full validation scan succeeds.

Narrowing or otherwise lossy type conversions are rejected in version one. Rename, drop, and
nullability changes require a typed confirmation containing the publication's physical name. Additive
and widening changes require ordinary confirmation. Every accepted mutation executes as one DuckLake
DDL transaction and records the resulting schema fingerprint in Postgres only after DuckLake commits.
If the Postgres update fails, Atlas pauses the publication and repairs by reading the authoritative
DuckLake schema; it does not reverse the committed DDL with a compatibility shim.

Publication bindings map query output aliases to stable publication field UUIDs. A physical rename
therefore does not change historical query SQL. Dropped fields are removed from materialized output;
historical bindings remain reproducible for their surviving fields. New non-null fields require a
default or compatible replacement bindings before the DDL is accepted.

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

For live work, the runtime resolves matching publication bindings after page acquisition and freezes
the resulting plans into the crawl ingestion job. The repository worker first commits the crawl and
DOM evidence. It then publishes deterministic live materialization jobs and only afterward
acknowledges the ingestion message. A crash after the evidence commit but before publication causes
ingestion redelivery; the already-idempotent crawl commit is recognized and the same stable
materialization jobs are published again. A broken publication can therefore fail independently
without preventing source evidence from becoming durable.

Failed crawls and successful crawls without a document do not produce materialization jobs; their
skipped reason remains visible in the ingestion result.

Materialization uses two additional subjects in the existing repository work stream:

- `atlas.repository.materialize.live` for newly committed crawls;
- `atlas.repository.materialize.backfill` for explicit backfills and corrections.

Only the repository worker consumes either subject and writes DuckLake. A job contains one
publication, a frozen plan, and at most 100 crawl IDs by default; the bound is configurable within the
NATS envelope and repository staging limits. The worker drains live ingestion and live materialization
preferentially, but after ten live batches it accepts one waiting backfill batch. One bounded backfill
batch is the maximum delay imposed on newly arriving work. These are subjects and durable consumers
in the existing repository stream, not another writer or state owner.

Backfill planning pages through crawl IDs in deterministic `(captured_at, crawl_id)` order and
publishes frozen batches. Current run and batch state lives in NATS KV. Successful analytical history
lives in DuckLake; Postgres retains only the editable definition and optional provenance UUIDs.

DuckLake keeps two private analytical tables in `main`:

- `publication_runs` records one live, backfill, or correction run, its frozen scope, initiating
  provenance, start and finish times, counts, and terminal outcome;
- `publication_attempts` records one publication/crawl attempt, its operation identity, binding and
  query revision, row-change counts, warnings, errors, and terminal outcome.

These tables make zero-row, skipped, and failed derivations inspectable without putting durable run
history in Postgres. They are written only by the repository worker and are not public publication
tables.

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

Live materialization is never paused behind an entire backfill. Because each crawl is reconciled by
its effective logical key, live and historical batches may interleave safely. Two jobs targeting the
same publication and crawl have the same stable operation identity; NATS KV compare-and-swap and the
repository reconciliation make redelivery idempotent. A correction submitted for a crawl already in
flight waits for that stable operation to reach a terminal state before publishing its successor.

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

The default exact-replay retention target is 30 days and is configurable per deployment. Snapshot
maintenance does not pin history indefinitely for lagging consumers: consumer lag is warned at 50%
of the retention window, critical at 80%, and becomes an explicit `CDC_GAP` when required history has
expired. Operators may increase retention before a planned outage or backfill. A consumer outside the
window must snapshot-bootstrap and resume from the bootstrap snapshot.

Snapshot bootstrap belongs to DuckLake CDC rather than a custom Atlas cursor API. The CDC extension
and Python client must provide one operation that:

1. chooses and pins a committed snapshot `S`;
2. creates or resets the table consumer to start strictly after `S`;
3. exposes the publication table as of `S` through a dedicated read connection;
4. releases the snapshot only after the caller confirms its snapshot sink succeeded;
5. then permits ordinary CDC reads after `S`.

Atlas exposes publication connection metadata and diagnostics but does not proxy downstream DML or
store a second consumer cursor.

## Correctness invariants

- No publication row becomes visible before its supporting crawl evidence is durable.
- Failure of publication materialization does not roll back or hide already durable crawl evidence.
- Materializing the same frozen input more than once is idempotent.
- Query revisions used by a run do not change while it executes.
- Publication row identity is independent of the query revision that produced it.
- A correction emits only the inserts, updates, and deletes required to reconcile derived state.
- DuckLake is authoritative for views and publication rows; Postgres references never mirror their
  SQL or data.
- Consumer notifications may be lost without losing publication data or replayability.
- A consumer advances its cursor only after its required downstream work succeeds.
- Missing retained CDC history is an explicit gap requiring replay reset or snapshot bootstrap.
- DuckLake CDC is the external publication change boundary; Atlas does not use it to schedule internal
  materialization work in version one.

## Views lifecycle

Every view created through Atlas receives a Postgres catalogue-view reference. A DuckLake view
created directly is visible through catalogue discovery but has no Atlas ownership or descriptive
metadata until explicitly adopted. Adoption stores its `view_uuid` and current qualified name after
verifying that the view is in the `views` schema.

Create and adopt operations never copy SQL into Postgres. Atlas reads the current definition from
DuckLake when displaying or editing a view. Replacing a view executes DuckLake DDL and leaves the
stable Atlas reference attached to the resulting DuckLake identity returned by the operation.

Cross-store failure is handled as reference repair, not dual authority:

- an Atlas-created DuckLake view whose Postgres reference was not committed appears as unowned and
  may be adopted;
- a reference whose DuckLake view was dropped appears unavailable and may be detached;
- Atlas never recreates a missing view from Postgres because Postgres does not contain its SQL.

Dropping a referenced view requires an explicit impact preview. Atlas first drops the authoritative
DuckLake object and then archives the Postgres reference. An interrupted operation converges through
the unavailable-reference repair path.

## Application surface

HTTP and CLI adapters expose the same application services. Exact Pydantic shapes live beside their
implementation, but version one provides these operations:

- create, list, inspect, edit, archive, and restore saved queries;
- list query revisions and run any retained revision interactively;
- create, discover, adopt, replace, detach, and drop catalogue views;
- create and inspect draft publications and their fields;
- create, validate, preview, activate, and archive query bindings;
- activate, pause, resume, archive, and inspect publications;
- preview and apply supported publication schema changes;
- start bounded backfill or correction runs and inspect their NATS-backed current state;
- inspect durable publication runs and attempts from DuckLake;
- obtain the qualified DuckLake table identity, schema, keys, schema fingerprint, CDC readiness, and
  retention diagnostics needed by an external consumer.

Mutating adapters validate and translate only. Query revisioning, binding resolution, schema-change
rules, job freezing, reconciliation, and repair behavior live in control, action, runtime, and
repository modules according to the existing Atlas boundaries.

## Version-one acceptance criteria

The feature is ready when all of the following hold:

- editing a saved query retains and reruns every immutable revision;
- a DuckLake view remains usable without an Atlas reference, and adoption never duplicates its SQL;
- one publication accepts two query revisions for different captured-time ranges without changing
  its physical table;
- every publication evaluation is demonstrably scoped to one supplied crawl ID;
- crawl evidence commits successfully even when its publication materialization later fails;
- redelivery after crashes between evidence commit, job publication, publication commit, and message
  acknowledgement produces no duplicate logical rows or lost jobs;
- a bounded backfill can interleave with live work and converge to the same table as chronological
  materialization;
- a correction emits the expected insert, update, and delete changes and no changes for equal rows;
- every supported DDL mutation produces a CDC schema boundary and materialization resumes only with
  the matching schema fingerprint;
- a consumer can snapshot-bootstrap at `S`, consume strictly later changes, restart, and replay a
  previously uncommitted window;
- expired history produces `CDC_GAP` and never silently falls back to a snapshot;
- publication rows, runs, and attempts trace back to query binding, query revision, crawl, document,
  and raw HTML evidence;
- `make check` covers the control and repository contracts, plus a low-volume end-to-end crawl,
  materialization, CDC, schema-change, replay, and backfill scenario.

## Version-one delivery sequence

1. Add versioned saved queries and interactive revision history.
2. Add user-defined DuckLake views without a second SQL authority.
3. Promote a query revision into a typed publication definition.
4. Materialize newly committed crawls into an observation publication.
5. Add bounded backfill and correction runs using the same materializer.
6. Expose publication tables through snapshot reads and DuckLake CDC.
7. Add snapshot bootstrap, schema-boundary, retention, and consumer diagnostics.

Each step ships only when its active caller exists. Steps do not introduce compatibility aliases or
dual storage paths for earlier development contracts.

## Deferred by decision

The following are resolved as outside version one rather than left as open design questions:

- Maintained current-state publications are deferred; use a DuckLake view over observation rows.
- Publication identity mutation is deferred; create a new publication.
- Lossy type conversion is deferred; create a new field or publication.
- Atlas-managed destination connectors and pipeline schedules are out of scope.
- A remote Atlas CDC proxy is out of scope; consumers connect through DuckLake and DuckLake CDC.
- CDC-triggered internal materialization is out of scope; NATS remains the internal work owner.
