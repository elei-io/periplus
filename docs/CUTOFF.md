# Cutoff

This cutoff completes the transition from the current catalogue model to the schema and lifecycle
defined in `docs/`.

At the cutoff, Atlas has a working acquisition-to-materialization path. The query layer is
explicitly outside this milestone. Nothing retained for that future layer may require compatibility
with the superseded schema.

“Working end to end” in this milestone means:

```text
crawl plan -> immutable bytes -> ingest.* -> CDC -> material.*
```

Externally acquired HTML may enter at the immutable-byte
boundary and follows the same downstream path.

It does not include compilation, the `web.*` semantic interface, agents, user-defined views, or
maintained user extractions. A bounded read-only console may execute SQL directly against the
physical `ingest.*` and `material.*` relations; this inspection surface does not compile, rewrite,
persist, or manage SQL.

## Required outcome

Atlas can:

1. Run a crawl plan and retain immutable document bytes.
2. Commit terminal crawl, visit, attempt, step, and document evidence under `ingest.*`.
3. Relay committed changes through CDC.
4. Maintain the six Atlas-owned `material.*` relations:
   - `material.html_elements`
   - `material.jsonld_values`
   - `material.pages`
   - `material.page_observations`
   - `material.links`
   - `material.link_observations`
5. Recover from redelivery, retries, worker restarts, and temporarily unavailable materialization
   dependencies without corrupting or losing committed evidence.
6. Expose normal ingestion and materialization health, capacity, backlog, and failure signals.
7. Accept exact external HTML without bypassing ingestion or materialization.

Ingestion is complete without materialization. Materialization failure never changes ingestion
evidence or graph execution state.

## Direct replacement

This is a greenfield contract replacement, not a migration period.

- The new schemas, identities, queue envelopes, CDC subjects, worker ownership, and repository
  boundaries replace their predecessors directly.
- Development Postgres, DuckLake, NATS, and object-store state may be reset.
- There are no compatibility views, aliases, dual reads or writes, legacy subjects, fallback
  routes, deprecated fields, or translation layers.
- Names are changed completely across code, tests, configuration, metrics, APIs, and documentation.
- Superseded code is deleted in the same change that removes its last caller.

## Ingestion boundary

`ingest.*` contains only immutable observed evidence.

- A crawl is a terminal graph execution.
- A visit is one admitted destination and acquisition outcome.
- Attempts and steps belong to visits.
- A document is one visit-owned observation.
- `document_id` identifies the observation; `content_sha256` identifies immutable logical bytes.
- A visible document reference always points to durable repository bytes.
- DOM generation, JSON-LD extraction, URL indexing, link derivation, projection repair, and other
  rebuildable interpretation do not run in ingestion.

Every graph-run terminal transition durably schedules its `ingest.crawls` record. Every visit
ingestion transaction commits its visit, attempts, steps, and optional document reference
atomically.

## Materialization boundary

The materialization worker runs fixed Atlas-owned workloads rather than user-authored catalogue
materializations.

Two source workloads own the fixed projection stages:

```text
ingest.documents CDC -> material.html_elements
                     -> material.jsonld_values
                     -> material.links
                     -> material.link_observations
ingest.visits CDC    -> material.pages
                     -> material.page_observations
```

A workload acknowledges source changes only after its selected targets commit. Replay and repeated
delivery are idempotent. Local projection does not reserve a DuckBasin client; independent
partition writers borrow from the shared eight-client pool.

All fixed materializations are maintained from bounded CDC deltas. One pinned document selection
reads and parses each affected HTML body once and emits HTML, JSON-LD, link-pair, and link-
observation Arrow outputs. Enabled document outputs are grouped into at most eight stable writes
targeting roughly 32 MiB each. One pinned visit selection maintains pages and replaces visit-owned
observations exactly across inserts, corrections, and deletions.

The operations API can run any selected fixed table as a bounded backfill or shadow rebuild.
Tables are independently selectable, while selected tables owned by the same source share one
scan and projection pass. Cursors and high-water catch-up progress are durable in Postgres;
activation is a replay-safe atomic metadata swap.

No generic keyed, append, or full user-view materialization framework remains at this cutoff.
User-owned maintained extractions can be designed with the query layer later.

## Removed surfaces

The cutoff deletes, rather than repairs, every superseded catalogue-definition feature:

- Seeded views, table macros, scalar macros, materialized views, and their fixture loaders.
- View, table-macro, scalar-macro, and materialization management APIs.
- Their Postgres models, services, schemas, migrations where superseded, and controller state.
- Their frontend pages, dialogs, hooks, types, navigation, and status presentation.
- The generic catalogue materialization executor, lifecycle, bootstrap, refresh, and
  dematerialization paths.
- Old repository projection repair and ingestion-owned DOM paths.
- Active old schema-specific query, agent, fixture, benchmark, synthetic-load, and inspection paths
  that cannot operate on `ingest.*` and `material.*`.
- Old table names, schema diagrams, documentation, tests, metrics, and configuration.

No unavailable or knowingly broken endpoint remains registered. No dead model or abstraction is
kept merely because the future query layer might need something similar.

## Query boundary

The query layer and `web.*` semantic interface are not delivered by this cutoff. No time is spent
adapting, repairing, validating, or optimizing the Python compiler for the new ingestion and
materialization schemas. Compiler-facing query, optimization, agent, and user-defined data
workflows remain unavailable until the portable `web.*` catalogue is delivered. Direct read-only
physical SQL and schema autocomplete are explicitly not `web.*` behavior.

The superseded Python compiler and its schema-dependent integrations are
removed. Crawl-plan edges do not depend on the query layer: they execute only
against the current page's bounded navigation package.

## Exit criteria

The cutoff is complete only when all of the following are true:

- A clean deployment bootstraps and validates only the new Atlas-owned
  acquisition, ingestion, and materialization contract.
- The normal setup path completes without importing, initializing, invoking,
  or checking superseded compiler code or future query-layer code.
- A low-depth crawl with low concurrency produces correct `ingest.*` evidence and all applicable
  `material.*` results.
- Repeated content produces distinct document observations while reusing content-addressed bytes
  and projections correctly.
- Successful, failed, retried, and cancelled acquisition outcomes are represented correctly.
- Every terminal graph-run outcome produces exactly one terminal crawl record.
- Materialization replay produces the same logical result without duplicate identities or damaged
  aggregates.
- Worker restart and redelivery tests pass before commit, after commit but before acknowledgement,
  and while waiting for a dependency.
- Ingestion and materialization health, capacity, backlog, retry, and failure reporting work in the
  normal API and operational surfaces.
- The terminal and web consoles execute bounded read-only SQL against `ingest.*` and `material.*`,
  and autocomplete their current schemas without loading query-layer code.
- Repository-wide searches find no superseded table names, queue contracts,
  seeded definitions, management surfaces, projection repair, compatibility
  paths, compiler archive, or stale documentation.
- Backend checks and targeted ingestion, CDC, materialization, recovery, and worker lifecycle tests
  pass.
- The old lake and disposable control state can be deleted without losing any behavior that Atlas
  still claims to support.

There is no partial cutoff. If Atlas still carries an old path, exposes a broken old surface, or
requires a compatibility explanation, the cutoff has not been reached.
