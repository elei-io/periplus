# Cutoff

This cutoff completes the transition from the current catalogue model to the schema and lifecycle
defined in `docs_v2`.

At the cutoff, Atlas has a working acquisition-to-materialization path. The compiler is explicitly
outside this milestone. Nothing retained for the future compiler may require compatibility with the
superseded schema.

“Working end to end” in this milestone means:

```text
crawl graph -> immutable bytes -> ingest.* -> CDC -> material.*
```

It does not include compilation, the `web.*` semantic interface, agents, user-defined views, or
maintained user extractions. A bounded read-only console may execute SQL directly against the
physical `ingest.*` and `material.*` relations; this inspection surface does not compile, rewrite,
persist, or manage SQL.

## Required outcome

Atlas can:

1. Run a crawl graph and retain immutable document bytes.
2. Commit terminal crawl, visit, attempt, step, and document evidence under `ingest.*`.
3. Relay committed changes through CDC.
4. Maintain the four Atlas-owned `material.*` relations:
   - `material.html_elements`
   - `material.jsonld_values`
   - `material.pages`
   - `material.links`
5. Recover from redelivery, retries, worker restarts, and temporarily unavailable materialization
   dependencies without corrupting or losing committed evidence.
6. Expose normal ingestion and materialization health, capacity, backlog, and failure signals.

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

Each workload owns one CDC consumer and one target relation:

```text
ingest.documents CDC       -> material.html_elements
material.html_elements CDC -> material.jsonld_values
ingest.visits CDC          -> material.pages
ingest.documents CDC       -> material.links
```

A workload may read its dependencies but never writes another workload's target. It acknowledges
source changes only after its own target transaction commits. Replay and repeated delivery are
idempotent. A missing dependency is retried rather than treated as permanent failure.

No generic keyed, append, or full user-view materialization framework remains at this cutoff.
User-owned maintained extractions can be designed with the compiler later.

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
kept merely because a future compiler might need something similar.

## Compiler boundary

The compiler and `web.*` semantic interface are not delivered by this cutoff. No time is spent
adapting, repairing, validating, or optimizing the Python compiler for the new ingestion and
materialization schemas. Compiler-facing query, optimization, agent, and user-defined data
workflows remain unavailable until the new C compiler is implemented. Direct read-only physical
SQL and schema autocomplete are explicitly not compiler behavior.

The Python compiler is archived intact as reference material rather than deleted. Its implementation,
documentation, corpus, and focused tests move under a clearly non-runtime archive location. Archived
compiler material:

- Is not packaged as an Atlas runtime module.
- Is not imported by application or worker code.
- Has no API, CLI, agent, setup, fixture, or frontend entrypoint.
- Is not initialized or validated during setup or readiness.
- Is not executed by the active test suite or required to pass active lint and type checks.
- Does not constrain names, schemas, types, relationships, or implementation decisions in
  `ingest.*` or `material.*`.
- May be consulted when implementing the replacement compiler in C.

Active Python compiler integrations and schema-dependent runtime code are removed. The archive is
the sole exception to the rule that superseded code is deleted: it is inert source material, not a
supported dormant feature. The later C compiler starts from the established `ingest.*` and
`material.*` contracts.

## Exit criteria

The cutoff is complete only when all of the following are true:

- A clean deployment bootstraps and validates only the new Atlas-owned ingestion and materialization
  contract.
- The normal setup path completes without importing, initializing, invoking, or checking either the
  archived Python compiler or the future C compiler.
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
  and autocomplete their current schemas without loading compiler code.
- Repository-wide searches find no superseded table names, queue contracts, seeded definitions,
  management surfaces, projection repair, compatibility paths, or stale documentation outside the
  explicitly named Python compiler archive.
- Repository-wide dependency and import checks confirm that nothing outside the archive references
  the archived compiler.
- Backend checks and targeted ingestion, CDC, materialization, recovery, and worker lifecycle tests
  pass.
- The old lake and disposable control state can be deleted without losing any behavior that Atlas
  still claims to support.

There is no partial cutoff. If Atlas still carries an old path, exposes a broken old surface, or
requires a compatibility explanation, the cutoff has not been reached. The archived Python compiler
does not weaken this rule because it has no runtime, setup, test, packaging, or dependency path back
into Atlas.
