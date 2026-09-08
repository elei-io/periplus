# Architecture

This document describes the replacement continuous crawler in the working tree. Its complete
requirements are in [FRONTIER.md](FRONTIER.md). Coordinated deployment, development-state reset,
and live acceptance remain tracked in [FRONTIER_IMPLEMENTATION.md](FRONTIER_IMPLEMENTATION.md).
The removed graph execution model has no compatibility routes or runtime.

Periplus delivers one evidence path:

```text
manual or scheduled request selection -> immutable bytes -> ingest.* -> material.* -> public_v1.*
```

External HTML joins at the same immutable-byte boundary:

```text
```

## Package ownership

The monorepo delivers three independently buildable products under `packages/`:

- `periplus` owns crawl execution, durable evidence, catalogue maintenance and infrastructure APIs.
- `periplus-admin` owns operator workflows over administrative APIs.
- `periplus-public` owns the browser SQL interface over the query service.

Frontends consume HTTP contracts, never core Python modules or backing databases.
The privileged admin console executes request-scoped writable SQL through the control API
using a dedicated DuckLake connection; the public query process remains isolated and read-only.
Supporting SDK and shell packages contain client behavior only. Core remains usable without
both frontends. A separate query process owns SQL preparation and bounded execution over a
read-only DuckLake connection. Next.js owns web-specific agent orchestration, proxies all SQL to that process, and submits collections to the control API; neither frontend receives
lake credentials. The query process has no control-state, NATS, or writer credentials.

## Authorities

- Periplus Postgres owns editable control state, current collections, shared acquisitions, admission,
  selection checkpoints, budgets, policies, and the transactional frontier outbox.
- NATS JetStream and KV own frontier, ingestion, and materialization work delivery, worker presence,
  operation leases, and per-domain pacing. They are not authoritative frontier or materialization
  state.
- The object repository owns immutable content-addressed source bytes.
- DuckLake owns historical observed evidence, rebuildable Periplus materializations, and the portable
  public `public_v1.*` catalogue. Its metadata store is a separate authority from Periplus
  Postgres.

Current frontier execution never moves into DuckLake. Crawl history never moves into Periplus Postgres.
The local `lake-postgres` service stores DuckLake metadata only; the local `periplus-postgres`
service stores Periplus control state only.

## Installation and client lifecycle

`periplus-setup` is the only catalogue installer. It attaches DuckLake, reconciles the physical
schemas, and transactionally installs the complete persistent `public_v1.*` contract.
Ordinary Periplus processes validate the installed contract and never repair it.

Periplus uses the standard DuckDB runtime and official storage extensions. The query API owns
query validation and future optimizations. One DuckLake connection factory owns storage-protocol
configuration and attachment for every Periplus process; only live materialization loads the
separate DuckLake CDC extension.
Filesystem and S3 are built-in protocols; callers may inject a protocol for another DuckDB-supported
data URI without adding storage branches to catalogue or materialization workflows.
Periplus services are needed only to acquire, ingest, or materialize more data. Stopping those
services leaves the complete analytical lake intact. Deleting Periplus Postgres removes editable
collection intent, controls, and current execution state, but never historical evidence already committed to
DuckLake. Deleting the DuckLake metadata store destroys the catalogue even when its immutable S3
objects remain.

## Process ownership

- The crawler acquires one page, stores immutable bytes, publishes visit evidence, and advances
  frontier work without waiting for catalogue ingestion.
- Ingestor replicas consume one shared durable lane without a leader. Each process owns one NATS
  session and a configurable bounded set of DuckLake writer lanes; each lane has its own catalogue
  connection.
- The materializer scans a pinned `ingest.visits` snapshot into bounded visit batches and maintains
  the complete fixed `material.*` generation, then follows inserted visits through DuckLake CDC.
- The janitor removes Periplus-owned transient navigation/runtime state and owns opt-in
  request retention, logical evidence retirement and snapshot-safe raw-object reclamation
  ([RETENTION.md](RETENTION.md)). LakeDucktor retains physical lake-file ownership.

Ingestion does not wait for materialization. A visit is the single unit of rebuild work; its
optional document is projected in that same batch. There is no document lane, target-to-target
chain, or authoritative queue ledger.

The ingestion durable consumer has a code-owned global delivery ceiling independent of replica
or lane count. This keeps cluster backpressure stable while allowing replicas to add useful write
capacity. Transaction conflicts are retried locally with the same frozen evidence under the same
request-scoped operation lease. A replica failure leaves the durable delivery unacknowledged, so
another symmetric replica resumes it.

## Materialization

Periplus Postgres stores rebuild control state and bounded batch identities. JetStream delivers plan, visit
batch, and serialized activation work; messages are ACKed only after their corresponding durable
commit. Periplus Postgres records successful publication once; recovery publishes only unpublished work,
while JetStream redelivers published work until ACK. Any worker replica can consume a visit batch.

Large projected relations are written under the permanent `material/data` object namespace and
registered with `ducklake_add_data_files`. Registered files never occupy the transient rebuild
namespace under `material/staging`, and Periplus never deletes them; LakeDucktor alone reclaims
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
marker is visible. There is one outstanding CDC window and no Periplus Postgres live-work ledger.

An unreadable registered material file invalidates the complete active generation. The worker
durably reuses or creates one Periplus Postgres rebuild, removes the active-generation marker to stop CDC,
and ACKs the poisoned delivery because immutable `ingest.*` evidence is the rebuild authority.
The hidden rebuild then replaces all discovered material tables atomically. Local retries are
bounded so one bad delivery cannot occupy a worker lane indefinitely.

Runtime `public_v1.*` objects belong to a separate lightweight public-catalogue registry.
Its entries reference SQL resources and declare required material relations. Installation fails if
any dependency of the public contract is missing; runtime SQL is never part of a
materialization declaration.

## Shared frontier and finite collections

A collection freezes URL, description, or corpus-SQL seed intent and bounded page-local follow SQL.
Its URL interests are deduplicated across the whole collection. Compatible pending work shares an acquisition across public, system, and admin requests.
`request_class` describes intent and never partitions evidence. Participants freeze at dispatch.
Later requests can reuse eligible recent observations without changing the original capture cause.
Each collection retains its own first-admitted traversal context, depth, page budget, and settlement.

The crawler owns independent dispatch, acquisition, selection, publication, and recovery loops.
Before authorizing a physical attempt, capture checks the existing ingestion connection, stream,
and result-store handles with a five-second bound. Known delivery failure releases the unstarted
physical reservation and returns work to a thirty-second retry with an explicit waiting reason.
This does not wait for an ingestor or materializer and does not prove future publication success.
Every dispatched URL, regardless of seed or follow source, also passes a public-address preflight
before domain pacing and attempt authorization. Non-public destinations are cancelled without a
physical attempt; unavailable DNS defers work. DNS lookups have a ten-second caller deadline and
four outstanding slots per event loop; timed-out lookups retain a slot until the resolver finishes.
Protection against redirects, subresources, other targets, and DNS rebinding reaching sensitive
internal services belongs at the CDP deployment's network boundary. The configured Stolosio
deployment's isolation has not been verified; Periplus's initial DNS check does not establish it.
Short PostgreSQL transactions reserve global physical budgets and collection page units. Time-dependent
control transitions read PostgreSQL wall-clock time after acquiring the control row. Lock waiting
cannot extend an expired authorization or consume part of a newly issued lease.
Independent outbox publication and retry transitions likewise lock their row before checking
expiry. Collection, outbox, and receipt claims use a database-time selection bound and grant lease
duration only after their bounded selection has acquired the rows. NATS
operation leases suppress duplicate execution; per-domain permits enforce website pacing.
Timeout after authorization can leave an uncertain remote attempt, so recovery records uncertainty
and charges the conservative bound rather than claiming exactly-once physical execution.

Description discovery runs in bounded checkpointed passes in the crawler. Corpus seed SQL uses
the isolated query service; follow SQL uses only the current page's bounded navigation package.
Reusable request definitions and interval/cron schedules create ordinary bounded
collections. The crawler evaluates due schedules transactionally; there is no
separate background selection or allocation lane. See [SCHEDULES.md](SCHEDULES.md).

Immutable observations, attempts, collection definitions/outcomes, fulfillments, and acquisition
reasons travel through the ingestion lane into DuckLake. Current item views read bounded control
state; arrivals and historical collection views read bounded durable evidence. Readiness is a
separate active-generation proof, not an inference from settlement or queue acknowledgement.

The detailed contracts are in [SCHEMA.md](SCHEMA.md), recovery and materialization in
[LIFECYCLE.md](LIFECYCLE.md), and bounded SQL
in [QUERY.md](QUERY.md). The remaining cutover gates are listed in
the implementation ledger; do not start replacement services against an old control schema.

### Private query execution history

[QUERY_HISTORY.md](QUERY_HISTORY.md) defines the 30-day private `query_executions`
table in control Postgres, bounded best-effort recording, janitor cleanup and the
`observatory/queries` dashboard. This is explicitly approved product analytics;
no query results or crawl history are added to control Postgres.
