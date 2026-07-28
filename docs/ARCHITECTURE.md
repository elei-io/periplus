# Architecture

Atlas delivers one evidence path:

```text
crawl plan -> immutable bytes -> ingest.* -> CDC -> material.* -> web.* / dom.*
```

External HTML joins at the same immutable-byte boundary:

```text
external evidence -> immutable bytes -> ingest.* -> CDC -> material.* -> web.* / dom.*
```

## Authorities

- Postgres owns editable control state, current graph execution, admission, progress, schedules,
  policies, and the transactional graph outbox.
- NATS JetStream and KV own work delivery, worker presence, operation leases, per-domain pacing,
  and CDC events. They are not authoritative graph state.
- The object repository owns immutable content-addressed source bytes.
- DuckLake owns historical observed evidence, rebuildable Atlas materializations, and the portable
  public `web.*` and `dom.*` catalogue.

Current graph execution never moves into DuckLake. Crawl history never moves into control-plane
Postgres.

## Process ownership

- Acquisition acquires one page, stores immutable bytes, publishes visit evidence, and advances
  graph work without waiting for catalogue ingestion.
- Ingestion commits immutable crawl and visit evidence under `ingest.*`.
- CDC carries committed DuckLake changes into bounded projection work.
- Materialization maintains fixed rebuildable `material.*` relations.
- Housekeeping removes only Atlas-owned transient navigation and runtime state.

Ingestion does not wait for materialization. Every target derives from its ingestion source; there
is no target-to-target materialization chain.

## Crawl-plan boundary

Crawl plans are editable Postgres definitions. A run freezes its complete plan configuration
before admitting one root URL into durable crawl requests. Current run, request, and edge-evaluation
state remains in Postgres. A terminal run schedules its immutable `ingest.crawls` evidence through
the transactional graph outbox and ordinary ingestion path.

Plan edges select URLs only from the navigation package derived from the page
that just completed acquisition. Historical catalogue joins are not an
acquisition-plan capability. URL admission is always deduplicated across the
complete run.

The detailed data contract is in [`SCHEMA.md`](SCHEMA.md), execution and recovery semantics are in
[`LIFECYCLE.md`](LIFECYCLE.md), external loading is in [`IMPORTS.md`](IMPORTS.md), and the portable
query boundary is in [`QUERY.md`](QUERY.md), and plan semantics are in
[`CRAWL_PLANS.md`](CRAWL_PLANS.md).
