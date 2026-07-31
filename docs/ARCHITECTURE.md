# Architecture

Atlas delivers one evidence path:

```text
crawl plan -> immutable bytes -> ingest.* -> material.* -> web.* / dom.*
```

External HTML joins at the same immutable-byte boundary:

```text
external evidence -> immutable bytes -> ingest.* -> material.* -> web.* / dom.*
```

## Authorities

- Postgres owns editable control state, current graph execution, admission, progress, schedules,
  policies, and the transactional graph outbox.
- NATS JetStream and KV own graph, ingestion, and materialization work delivery, worker presence,
  operation leases, and per-domain pacing. They are not authoritative graph or materialization
  state.
- The object repository owns immutable content-addressed source bytes.
- DuckLake owns historical observed evidence, rebuildable Atlas materializations, and the portable
  public `web.*` and `dom.*` catalogue.

Current graph execution never moves into DuckLake. Crawl history never moves into control-plane
Postgres.

## Installation and client lifecycle

`atlas-setup` is the only catalogue installer. The Atlas image contains an exact-version native
extension compiled in a builder stage. Setup loads that extension, attaches DuckLake, reconciles
the physical schemas, and transactionally installs the complete persistent `web.*` and `dom.*`
contract. Ordinary Atlas processes validate the installed contract and never repair it.

The extension binary executes inside each DuckDB client and is not stored in DuckLake. The SDK and
direct shell therefore load a matching host artifact before attaching the lake. Once attached,
queries read the persistent catalogue and lake data directly; no running Atlas API is required.
One DuckLake connection factory owns extension loading, storage-protocol configuration, and
attachment for every Atlas process. Filesystem and S3 are built-in protocols; callers may inject a
protocol for another DuckDB-supported data URI without adding storage branches to catalogue or
materialization workflows.
Atlas services are needed only to acquire, ingest, or materialize more data. Stopping those
services leaves the complete analytical lake intact. Deleting control-plane Postgres separately
would remove editable plans, schedules, and current execution state, but never the historical
evidence already committed to DuckLake.

## Process ownership

- Acquisition acquires one page, stores immutable bytes, publishes visit evidence, and advances
  graph work without waiting for catalogue ingestion.
- Ingestion replicas consume one shared durable lane without a leader. Each process owns one NATS
  session and a configurable bounded set of DuckLake writer lanes; each lane has its own catalogue
  connection.
- Materialization scans a pinned `ingest.visits` snapshot into bounded visit batches and maintains
  the complete fixed `material.*` generation, then follows inserted visits through DuckLake CDC.
- Housekeeping removes only Atlas-owned transient navigation and runtime state.

Ingestion does not wait for materialization. A visit is the single unit of rebuild work; its
optional document is projected in that same batch. There is no document lane, target-to-target
chain, or authoritative queue ledger.

The ingestion durable consumer has a code-owned global delivery ceiling independent of replica
or lane count. This keeps cluster backpressure stable while allowing replicas to add useful write
capacity. Transaction conflicts are retried locally with the same frozen evidence under the same
request-scoped operation lease. A replica failure leaves the durable delivery unacknowledged, so
another symmetric replica resumes it.

## Materialization

Postgres stores rebuild control state and bounded batch identities. JetStream delivers plan, visit
batch, and serialized activation work; messages are ACKed only after their corresponding durable
commit. Postgres records successful publication once; recovery publishes only unpublished work,
while JetStream redelivers published work until ACK. Any worker replica can consume a visit batch.

Large projected relations are written under the permanent `material/data` object namespace and
registered with `ducklake_add_data_files`. Registered files never occupy the transient rebuild
namespace under `material/staging`, and Atlas never deletes them; LakeDucktor alone reclaims
unreferenced physical data.

Every Python module under `materialization/projections/` is one complete fixed projection
declaration. Discovery is the registry: adding, editing, or deleting a materialization means
adding, editing, or deleting that one module, followed by redeployment and a complete rebuild.
Workers build one shared parse context and append registry-validated files; there is no keyed
replacement or target-specific commit path. File registration and the applied-batch marker commit
in one DuckLake transaction, making commit-before-ACK redelivery a no-op.

Every rebuild creates every discovered hidden material table, catches up visits inserted after the
pinned source snapshot, validates the registry digest, and renames the complete generation
atomically. Activation has a
single-consumer delivery lane only to serialize the metadata swap; projection throughput remains
horizontally scalable.

After activation, one insert-only DuckLake CDC consumer follows `ingest.visits`. Every worker
replica is eligible to coordinate it; a renewable NATS operation lease elects one candidate, and
that connection then holds the DuckLake consumer's owner-token lease. A failed holder is replaced
after lease expiry without moving or resetting the DuckLake cursor. The elected coordinator
publishes deterministic visit batches to the same JetStream subject used by rebuild workers.
Workers apply each batch directly to the active generation and record the same applied-batch
marker in the material transaction. The coordinator advances the CDC cursor only after every
marker is visible. There is one outstanding CDC window and no Postgres live-work ledger.

An unreadable registered material file invalidates the complete active generation. The worker
durably reuses or creates one Postgres rebuild, removes the active-generation marker to stop CDC,
and ACKs the poisoned delivery because immutable `ingest.*` evidence is the rebuild authority.
The hidden rebuild then replaces all discovered material tables atomically. Local retries are
bounded so one bad delivery cannot occupy a worker lane indefinitely.

Runtime `web.*` and `dom.*` objects belong to a separate lightweight public-catalogue registry.
Its entries reference SQL resources and declare required material relations. Missing projection
dependencies disable and remove the corresponding public objects; runtime SQL is never part of a
materialization declaration.

## Crawl-plan boundary

Crawl plans are editable Postgres definitions. A run freezes its complete plan configuration
before admitting one or more start URLs into durable crawl requests. Current run, request, and
edge-evaluation state remains in Postgres. A terminal run schedules its immutable `ingest.crawls`
evidence through the transactional graph outbox and ordinary ingestion path.

Plan edges select URLs only from the navigation package derived from the page
that just completed acquisition. Historical catalogue joins are not an
acquisition-plan capability. URL admission is always deduplicated across the
complete run.

The detailed data contract is in [`SCHEMA.md`](SCHEMA.md), execution and recovery semantics are in
[`LIFECYCLE.md`](LIFECYCLE.md), external loading is in [`IMPORTS.md`](IMPORTS.md), and the portable
query boundary is in [`QUERY.md`](QUERY.md), and plan semantics are in
[`CRAWL_PLANS.md`](CRAWL_PLANS.md).
