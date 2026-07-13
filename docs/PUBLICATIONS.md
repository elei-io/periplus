# Catalogue Definitions, Materialization, and Publication

Status: accepted target design. Queries, revisions, views, and live document- or crawl-scoped view
materialization exist. The independent materialization-worker cutover, advanced scope adapters, and
publications remain pending.

Atlas turns retained web evidence into tabular data. This document defines the layers between SQL
exploration, reusable definitions, durable derived data, and external consumption.

## Product model

Atlas has four control-plane catalogue entities:

```text
catalogue_queries
catalogue_query_revisions
catalogue_view_references
catalogue_materializations
```

They support three user-facing concepts:

```text
Query
View ── 0..1 live Materialization ── Publication contract(s)
```

Publication cardinality is intentionally deferred. The important invariant is that every publication
references an existing materialization and never creates another copy of its data.

Queries are reusable SQL drafts. Views are reusable catalogue definitions. Materialization is an
optional live capability of a view, and a publication is an external contract over that durable
result.

There is no standalone “Materialized Views” product section. Users encounter materialization while
working with a view.

## Queries

A query is user-authored SQL stored by Atlas for exploration and reuse.

- `catalogue_queries` owns the stable definition identity, name, description, and archive state.
- `catalogue_query_revisions` contains immutable SQL revisions.
- Editing a query creates a revision; it never mutates historical SQL.
- A revision may be run interactively or saved as a view.
- Saving a query alone does not create durable derived data.
- Queries cannot be materialized. A durable reusable result starts by saving the SQL as a view.

## Views

A view is a real DuckLake SQL view stored in the `views` schema.

- DuckLake owns the SQL, view UUID, schema, snapshot validity, and lifecycle of the view object.
- `catalogue_view_references` gives Atlas a stable control-plane identity for ownership, display
  metadata, discovery, and dependent materialization references.
- Atlas does not present user-visible revision history for views.
- A non-materialized view is evaluated on every read.
- Adopting a DuckLake view creates only an Atlas reference; it does not copy the SQL into Postgres.

Replacing a view changes its DuckLake definition identity. A managed view detail URL uses the stable
Atlas reference ID, so ordinary edits do not invalidate navigation or dependent control-plane
references.

If a view has a materialization, editing its SQL pauses live updates and existing-data backfill.
Atlas keeps serving the previous durable result until the user rebuilds the materialization from the
new definition. A schema-changing edit requires dematerializing and activating materialization again.

A materialized view cannot be detached or dropped. The user must dematerialize first.
Dematerialization removes only the durable backing table and its maintenance state; the same view
name remains available as a virtual view.

## One materialization per view

A view may have at most one materialization.

This is a product and storage invariant, not merely a UI convention. Creating a second durable copy
from the same definition wastes storage, complicates lifecycle controls, and makes it unclear which
result should be published.

The control-plane schema enforces:

```text
UNIQUE(view_reference_id)
```

The materialization retains the source SQL, stable Atlas view reference, live definition binding,
and activation snapshot boundary.

`catalogue_materializations` remains a separate table because its lifecycle is substantial. Folding
these fields into the view reference would mix definition identity with a substantial operational
lifecycle. The materialization owns:

- the stable physical DuckLake table identity;
- the document or crawl discriminator used for incremental maintenance;
- the active definition binding and provenance boundaries;
- scope configuration;
- activation snapshot and durable coverage;
- live CDC position and activation-backfill progress;
- partitioning and compaction metadata;
- failure state and retry administration;
- storage statistics; and
- fenced dematerialization state.

An attempt to materialize a view that already has one returns or opens the existing
materialization. Configuration changes update that attachment; they never create a second table.

## User experience

The main Catalogue navigation contains SQL, Queries, and Views. Materialization is visible only on
views.

The view list shows whether each definition is evaluated virtually or backed by durable data:

| Definition | Evaluation | State | Rows | Storage |
| --- | --- | --- | ---: | ---: |
| Page links | Materialized | Live | 416,495 | 12.3 MB |
| Latest crawls | Materialized | Catching up | 385 | 197 KB |
| Document text | On read | Virtual | — | — |

Each query and view has a dedicated detail page led by its SQL and returned columns. Queries offer a
**Save as view** action and cannot be materialized. A virtual view presents a **Materialize** action.
Materialization activation and status open in a bottom sheet with a light dismissible backdrop so
operational detail does not compete with the view definition. The sheet leads with:

- current state and whether Atlas is caught up;
- pending and failed scopes;
- stored size and row count;
- activation-backfill progress;
- last successful settlement;
- rebuild and retry administration when required; and
- dematerialization.

There may be a canonical internal route such as `/catalogue/materializations/:id` for deep links and
administration, but materializations are not advertised as a separate primary catalogue resource.

Materialization activation stays on the owning view detail page and opens the materialization sheet.

## Dematerialization

“Delete materialization” is presented as **Dematerialize**.

Dematerialization removes the managed DuckLake table and its coverage while preserving the view
definition. It is a fenced repository operation:

1. Atlas disables live discovery and backfill.
2. The definition is marked for dematerialization.
3. Queued commits reject the stale definition revision.
4. A materialization worker drops the DuckLake table and durable coverage.
5. Atlas archives the materialization attachment.

DuckLake snapshots may retain physical Parquet files until the configured retention window expires.
Dematerializing and later materializing again creates a new physical table lifecycle, but there is
never more than one active attachment for the definition.

## Live scoped materialization

Scoped incremental maintenance evaluates one bounded unit at a time. The first supported scope is an
immutable document; crawl scope follows the same model.

```text
document scope → $document_id
crawl scope    → $crawl_id
```

A definition needs both:

1. a safe way to restrict evaluation to one supplied scope; and
2. a way for Atlas to identify which durable rows belong to that scope for atomic replacement.

The simple contract uses a user-selected result column. Its physical name does not matter:

```sql
SELECT *
FROM views.page_links
WHERE any_document_column = $document_id;
```

The user maps `any_document_column` to document scope. Atlas stores the mapping rather than relying
on a reserved user-facing column name.

An advanced definition may use validated parameterized SQL to restrict one scope without exposing
the scope value publicly. Atlas then adds an internal `_atlas_scope_id` to staged results. Arbitrary
string interpolation is forbidden. Atlas validates the named parameter, read-only query shape,
result ownership, and execution bounds before activation.

A view is eligible for incremental materialization when it exposes a supported scope column or has a
validated scope adapter. Atlas evaluates a wrapper such as:

```sql
SELECT *
FROM views.page_links
WHERE document_key = $document_id;
```

Atlas validates the output mapping and enforces runtime row, byte, and memory bounds. Predicate
pushdown is desirable but is not treated as a correctness guarantee; a scope that exceeds limits
fails visibly instead of exhausting the service. Representative plan inspection and an explicit
per-scope timeout remain hardening work.

Atlas exposes no manual-refresh materialization mode. **Materialize** always means “create and keep
this view updated live.” If Atlas cannot prove a supported document- or crawl-scoped evaluation,
activation is disabled and names the missing discriminator or unsupported SQL construct. A manually
refreshed snapshot would be a different product concept and is not represented as a materialization.

## Activation, live maintenance, and backfill

Activation freezes a DuckLake snapshot and creates the target table. Live discovery begins strictly
after that boundary. Historical backfill enumerates scopes visible at the activation snapshot.

Live discovery and activation backfill are independent internal stages:

- live work consumes newly committed scope changes from a durable CDC cursor;
- backfill pages through historical scope IDs at a configurable rate;
- live work is prioritized so a long backfill does not block freshness;
- restarting a worker preserves the table, cursor, activation boundary, and completed coverage; and
- retry continues from durable state rather than rebuilding completed scopes.

Live, backfill, correction, and replay-driven rematerialization all execute the same bounded scope
definition. A durable coverage record marks a scope complete even when it produces zero rows.

CDC discovers work; it is not the execution queue. Scope work travels through JetStream. A supervised
materialization worker evaluates one scope and may stage bounded Arrow in repository object storage.
That worker verifies the checksum, fences the active definition, atomically replaces the scope in
DuckLake, and records coverage. It shares no process, connection, health state, or concurrency slot
with base ingestion.

Every scope job identifies `materialization_id`, `definition_revision_id`, `scope_kind`, and
`scope_id` together with its active query revision, target, scope column, and live/backfill source.
Before evaluation and again under a Postgres row lock before commit, Atlas verifies that the
materialization is active, its source attachment and definition revision are current, its target and
scope contract still match, and the corresponding live or backfill switch remains enabled. Stale
jobs are acknowledged without evaluation or durable failure records; staged output from a job that
becomes stale before commit is deleted.

## Partitioning and compaction

Managed materializations may declare a DATE or TIMESTAMP output column for physical partitioning.
Atlas uses year, month, and day transforms. Partitioning is never inferred from an unrelated timestamp
merely to obtain a layout.

Seeded document links carry immutable document ingestion time as `document_created_at` and are
partitioned by `year/month/day(document_created_at)`. Crawl-scoped observation tables use their
capture timestamp.

DuckLake owns analytical file layout. Repository compaction operates within partitions and remains
the only path that rewrites small files. Atlas does not create permanent application-owned Parquet
files per scope or crawl.

## Publications

A publication is an external consumer contract over an existing materialization.

- It does not own or copy the durable table.
- It exposes the materialization's stable table identity and declared row semantics.
- It adds an external schema contract, compatibility policy, CDC exposure, replay retention,
  snapshot bootstrap, connection metadata, and consumer diagnostics.
- Removing a publication does not dematerialize its source.
- Dematerializing a published definition requires first removing or explicitly disabling dependent
  publication contracts.

“Dataset” may be used in user-facing copy when clearer, but `publication` names the contract that
makes Atlas-managed durable data consumable outside Atlas.

Versioned query changes do not require a new publication when the physical schema and row semantics
remain compatible. Definition-boundary provenance identifies which query revision or view definition
produced every scope. Incompatible schema changes pause publication until the consumer-facing contract
is explicitly accepted or replaced.

DuckLake CDC is the publication replay boundary. NATS may reduce internal wake-up latency but is not
the external replay log. Consumers bootstrap from a consistent DuckLake snapshot and then continue
from the corresponding CDC position.

## Backfills, corrections, and replay

These operations are distinct:

1. **Historical backfill** evaluates source scopes that existed at activation but have not completed.
2. **Correction or rematerialization** reevaluates source evidence and may insert, update, or delete
   durable rows, producing legitimate CDC events.
3. **Consumer replay** rereads retained publication changes by resetting or creating a CDC consumer;
   it does not mutate the materialization.
4. **Snapshot bootstrap** reads the materialization at a consistent snapshot and then starts CDC
   strictly after that snapshot.

External pipeline retries are downstream behavior and are not Atlas rematerialization.

## State ownership

| Owner | Catalogue-related state |
| --- | --- |
| Postgres control plane | Queries, immutable query revisions, stable view references, the optional one-to-one live materialization attachment, active definition bindings, lifecycle controls, and future publication contracts |
| NATS JetStream/KV | Live and backfill scope delivery, operation leases, current materialization-worker progress, retries, and dead letters |
| DuckLake | Authoritative SQL views, typed materialization tables, durable scope coverage, snapshots, row history, DDL history, and crawl evidence |
| DuckLake CDC metadata | Durable publication consumer subscriptions, leases, cursors, and audit state |
| Repository objects | Immutable raw HTML and bounded temporary Arrow staging objects |

Prometheus and progress events remain observational and never become correctness state.

## Reserved names and catalogue layout

Atlas reserves these DuckLake schemas:

- `main` contains retained web evidence and catalogue helpers;
- `views` contains persistent user-defined DuckLake views, whether virtual or materialized;
- `_atlas_materializations` contains private backing tables for materialized views; and
- `published` is reserved for future publication-facing aliases or contract objects and never holds
  a second data copy.

Physical names use lower-case snake case, match `^[a-z][a-z0-9_]{0,62}$`, and are unique within their
schema. Display names and descriptions may change freely. Once published, a materialization's physical
identity is stable because downstream consumers bind to it.

Atlas reserves the `_atlas_` prefix for internal materialization and publication provenance columns.

## System boundaries

Atlas owns:

- query revisions and view references;
- the one-live-materialization-per-view invariant;
- bounded historical backfill and incremental evaluation;
- live maintenance, backfills, corrections, and rebuilds;
- durable table lifecycle, provenance, schema, and storage diagnostics; and
- publication contracts, CDC exposure, replay, and bootstrap.

Atlas does not own:

- destination credentials or connectors;
- downstream warehouse or data-transformation graphs;
- warehouse-specific merge behavior;
- sink retries or external exactly-once side effects; or
- a general workflow orchestration platform.

External tools may consume publications, but they remain responsible for moving data into destination
systems.

Atlas crawl graphs are a separate internal acquisition model. Their SQL edges read retained crawl
evidence and derive URL inputs for further crawl nodes; they are not publication consumers or
general downstream transformation graphs.

## Required invariants

- A view has at most one active `catalogue_materialization`; a query has none.
- A materialization has exactly one stable view source.
- Materialized rows identify the DuckLake definition boundary that produced them.
- A changed view definition never silently rewrites durable data.
- Every incremental evaluation is demonstrably bound to one supplied scope and has explicit resource
  limits.
- Scope replacement and coverage recording are atomic and idempotent.
- Ingestion workers write base evidence and navigation-critical system projections; materialization
  workers alone write user-materialized view scopes.
- Materialization never gates ingestion, navigation readiness, graph edges, or graph-run completion.
- Restarting workers does not lose the activation boundary, CDC cursor, or completed coverage.
- Dematerialization fences queued work before dropping the durable table.
- A publication references an existing materialization and never creates another data copy.
- Publication data remains replayable independently of NATS notification delivery.
