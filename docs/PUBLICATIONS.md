# Catalogue Definitions, Materialization, and Publication

Status: accepted target design. Queries, revisions, views, the one-materialization control-plane,
and document-scoped incremental query/view materialization are implemented. Crawl scope, advanced
scope adapters, and publications remain pending.

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
Query ── 0..1 Materialization ── Publication contract(s)
View  ── 0..1 Materialization ── Publication contract(s)
```

Publication cardinality is intentionally deferred. The important invariant is that every publication
references an existing materialization and never creates another copy of its data.

Queries and views are reusable definitions. A materialization is an optional durable capability of
one definition. A publication is an external contract over that durable result.

There is no standalone “Materialized Views” product section. Users encounter materialization while
working with a query or view.

## Queries

A query is user-authored SQL stored by Atlas for exploration and reuse.

- `catalogue_queries` owns the stable definition identity, name, description, and archive state.
- `catalogue_query_revisions` contains immutable SQL revisions.
- Editing a query creates a revision; it never mutates historical SQL.
- A revision may be run interactively or used as the active definition of the query's one optional
  materialization.
- Saving a query alone does not create durable derived data.

Materialization belongs to the stable query identity, not permanently to one revision. The
materialization records which immutable revision currently defines its rows.

Saving a new query revision does not silently change durable data. Atlas shows the definition as
ahead of its materialization and offers explicit choices:

- **Keep current revision** leaves the materialization unchanged.
- **Rebuild with new revision** recomputes every scope or performs a full refresh.
- **Continue from this boundary** applies the new revision to subsequent scopes and records the
  boundary in materialization provenance.

The boundary option is valid only when Atlas can preserve one table schema and the user accepts
that historical and subsequent scopes were produced by different definition revisions.

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

If a view has a materialization, Atlas warns before editing its SQL. A definition change pauses
incremental maintenance and requires an explicit choice equivalent to query revision changes:

- keep the existing durable result;
- rebuild it from the replacement view; or
- continue from an explicit definition boundary.

A query with durable data cannot be archived, and a source view cannot be detached or dropped.
The user must dematerialize first. Dematerialization removes only the durable table and its
maintenance state; the source definition remains available for a later materialization.

## One materialization per definition

A query or view may have at most one materialization.

This is a product and storage invariant, not merely a UI convention. Creating a second durable copy
from the same definition wastes storage, complicates lifecycle controls, and makes it unclear which
result should be published.

The control-plane schema enforces:

```text
UNIQUE(query_id)
UNIQUE(view_reference_id)
CHECK(exactly one of query_id or view_reference_id is present)
```

For a query-backed materialization, `active_query_revision_id` identifies the immutable revision
currently used to produce rows. For a view-backed materialization, the record retains the bound
DuckLake definition UUID and snapshot boundary in addition to the stable Atlas view reference.

`catalogue_materializations` remains a separate table because its lifecycle is substantial. Folding
these fields into both query and view records would duplicate behavior and create many unrelated
nullable columns. The materialization owns:

- the stable physical DuckLake table identity;
- refresh or incremental maintenance mode;
- the active definition binding and provenance boundaries;
- scope configuration;
- activation snapshot and durable coverage;
- live and backfill controls;
- partitioning and compaction metadata;
- failure state and retry administration;
- storage statistics; and
- fenced dematerialization state.

An attempt to materialize a definition that already has one returns or opens the existing
materialization. Configuration changes update that attachment; they never create a second table.

## User experience

The main Catalogue navigation contains SQL, Queries, and Views. Materialization is visible within
the two definition collections.

Query and view lists show whether each definition is evaluated virtually or backed by durable data:

| Definition | Evaluation | State | Rows | Storage |
| --- | --- | --- | ---: | ---: |
| Page links | Materialized | Live | 416,495 | 12.3 MB |
| Latest crawls | Materialized | Full refresh | 385 | 197 KB |
| Document text | On read | Virtual | — | — |

Each query and view has a dedicated detail page. A virtual definition presents a **Materialize**
action. A materialized definition embeds its durable-data panel on the same page, including:

- table identity and source definition;
- live and backfill status;
- activation coverage and failures;
- rows, files, storage, and partitioning;
- pause, resume, rate, refresh, and rebuild controls; and
- dematerialization.

There may be a canonical internal route such as `/catalogue/materializations/:id` for deep links and
administration, but materializations are not advertised as a separate primary catalogue resource.

Materialization creation lands on the owning query or view detail page with the durable-data panel
selected.

## Dematerialization

“Delete materialization” is presented as **Dematerialize**.

Dematerialization removes the managed DuckLake table and its coverage while preserving the query or
view definition. It is a fenced repository operation:

1. Atlas disables live discovery and backfill.
2. The definition is marked for dematerialization.
3. Queued commits reject the stale definition revision.
4. The repository worker drops the DuckLake table and durable coverage.
5. Atlas archives the materialization attachment.

DuckLake snapshots may retain physical Parquet files until the configured retention window expires.
Dematerializing and later materializing again creates a new physical table lifecycle, but there is
never more than one active attachment for the definition.

## Full and incremental maintenance

A materialization has one of two maintenance modes.

### Full refresh

Full refresh replaces the durable result by evaluating the complete source explicitly. It is useful
for small or naturally bounded definitions. Refresh is never hidden inside a read.

### Scoped incremental

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

## Activation, live maintenance, and backfill

Activation freezes a DuckLake snapshot and creates the target table. Live discovery begins strictly
after that boundary. Historical backfill enumerates scopes visible at the activation snapshot.

Live and backfill are independent controls:

- live work consumes newly committed scope changes from a durable CDC cursor;
- backfill pages through historical scope IDs at a configurable rate;
- live work is prioritized so a long backfill does not block freshness;
- pausing preserves the table, cursor, activation boundary, and completed coverage; and
- resuming continues from durable state rather than rebuilding.

Live, backfill, correction, and replay-driven rematerialization all execute the same bounded scope
definition. A durable coverage record marks a scope complete even when it produces zero rows.

CDC discovers work; it is not the execution queue. Scope work travels through JetStream. A supervised
materialization worker evaluates one scope and stages bounded Arrow in repository object storage. The
repository worker alone verifies the checksum, fences the active definition, atomically replaces the
scope in DuckLake, and records coverage.

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
| Postgres control plane | Queries, immutable query revisions, stable view references, the optional one-to-one materialization attachment, active definition bindings, lifecycle controls, and future publication contracts |
| NATS JetStream/KV | Live and backfill scope queues, repository commit work, current worker/progress state, retries, and dead letters |
| DuckLake | Authoritative SQL views, typed materialization tables, durable scope coverage, snapshots, row history, DDL history, and crawl evidence |
| DuckLake CDC metadata | Durable publication consumer subscriptions, leases, cursors, and audit state |
| Repository objects | Immutable raw HTML and bounded temporary Arrow staging objects |

Prometheus and progress events remain observational and never become correctness state.

## Reserved names and catalogue layout

Atlas reserves these DuckLake schemas:

- `main` contains retained web evidence and catalogue helpers;
- `views` contains persistent user-defined DuckLake views;
- `materialized` contains the one optional durable result attached to each query or view; and
- `published` is reserved for future publication-facing aliases or contract objects and never holds
  a second data copy.

Physical names use lower-case snake case, match `^[a-z][a-z0-9_]{0,62}$`, and are unique within their
schema. Display names and descriptions may change freely. Once published, a materialization's physical
identity is stable because downstream consumers bind to it.

Atlas reserves the `_atlas_` prefix for internal materialization and publication provenance columns.

## System boundaries

Atlas owns:

- query revisions and view references;
- the one-materialization invariant;
- bounded full and incremental evaluation;
- live maintenance, backfills, corrections, and rebuilds;
- durable table lifecycle, provenance, schema, and storage diagnostics; and
- publication contracts, CDC exposure, replay, and bootstrap.

Atlas does not own:

- destination credentials or connectors;
- downstream transformation graphs;
- warehouse-specific merge behavior;
- sink retries or external exactly-once side effects; or
- a general workflow orchestration platform.

External tools may consume publications, but they remain responsible for moving data into destination
systems.

## Required invariants

- A query or view has at most one active `catalogue_materialization`.
- A materialization has exactly one stable source definition.
- Query-backed durable rows identify the immutable query revision that produced them.
- View-backed durable rows identify the DuckLake definition boundary that produced them.
- A new definition revision never silently rewrites durable data.
- Every incremental evaluation is demonstrably bound to one supplied scope and has explicit resource
  limits.
- Scope replacement and coverage recording are atomic and idempotent.
- The repository worker is the only runtime DuckLake writer.
- Pausing or restarting workers does not lose the activation boundary, CDC cursor, or completed
  coverage.
- Dematerialization fences queued work before dropping the durable table.
- A publication references an existing materialization and never creates another data copy.
- Publication data remains replayable independently of NATS notification delivery.

## Materialization cutoff

The materialization naming and ownership cutoff is complete:

1. The control-plane model and API use catalogue materializations.
2. Materializations attach to stable query IDs or stable view-reference IDs, with unique constraints
   and an exactly-one-source check.
3. Durable-data status and controls live in query and view surfaces; there is no primary standalone
   Materialized Views navigation item.
4. “Materialize” is idempotent and destructive removal is explicitly dematerialization.
5. Query materializations bind an immutable revision and view materializations bind an exact
   DuckLake definition boundary.
6. Source definitions cannot be archived, detached, or dropped until their durable data has been
   dematerialized.

Document-column view scope mappings are implemented. Crawl scope, advanced scope adapters,
publication contracts, and external CDC bootstrap/replay remain separate implementation milestones.

The simplest end-to-end proof is one versioned query or managed view, one bounded materialization,
one historical backfill, continuous live maintenance, and one external CDC consumer that can
bootstrap and replay without creating another Atlas table.
