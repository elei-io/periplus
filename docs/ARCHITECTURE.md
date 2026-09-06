# Architecture

Periplus delivers one evidence path:

```text
crawl plan -> immutable bytes -> ingest.* -> material.* -> web.* / content.*
```

External HTML joins at the same immutable-byte boundary:

```text
external evidence -> immutable bytes -> ingest.* -> material.* -> web.* / content.*
```

## Package ownership

The monorepo delivers three independently buildable products under `packages/`:

- `periplus` owns crawl execution, durable evidence, catalogue maintenance and infrastructure APIs.
- `periplus-admin` owns operator workflows over administrative APIs.
- `periplus-public` owns the browser SQL interface over the query service.

Frontends consume HTTP contracts, never core Python modules or backing databases.
Supporting SDK and shell packages contain client behavior only. Core remains usable without
both frontends. A separate query process owns SQL preparation and bounded execution over a
read-only DuckLake connection. Next.js owns web-specific agent orchestration, proxies all SQL to that process, and sends coverage requests to the control API; neither frontend receives
lake credentials. The query process has no control-state, NATS, or writer credentials.

## Authorities

- Periplus Postgres owns editable control state, current graph execution, admission, progress,
  schedules, policies, and the transactional graph outbox.
- NATS JetStream and KV own graph, ingestion, and materialization work delivery, worker presence,
  operation leases, and per-domain pacing. They are not authoritative graph or materialization
  state.
- The object repository owns immutable content-addressed source bytes.
- DuckLake owns historical observed evidence, rebuildable Periplus materializations, and the portable
  public `web.*` and `content.*` catalogue. Its metadata store is a separate authority from Periplus
  Postgres.

Current graph execution never moves into DuckLake. Crawl history never moves into Periplus Postgres.
The local `lake-postgres` service stores DuckLake metadata only; the local `periplus-postgres`
service stores Periplus control state only.

## Installation and client lifecycle

`periplus-setup` is the only catalogue installer. It attaches DuckLake, reconciles the physical
schemas, and transactionally installs the complete persistent `web.*` and `content.*` contract.
Ordinary Periplus processes validate the installed contract and never repair it.

Periplus uses the standard DuckDB runtime and official storage extensions. The query API owns
query validation and future optimizations. One DuckLake connection factory owns storage-protocol
configuration and attachment for every Periplus process; only live materialization loads the
separate DuckLake CDC extension.
Filesystem and S3 are built-in protocols; callers may inject a protocol for another DuckDB-supported
data URI without adding storage branches to catalogue or materialization workflows.
Periplus services are needed only to acquire, ingest, or materialize more data. Stopping those
services leaves the complete analytical lake intact. Deleting Periplus Postgres removes editable
plans, schedules, and current execution state, but never historical evidence already committed to
DuckLake. Deleting the DuckLake metadata store destroys the catalogue even when its immutable S3
objects remain.

## Process ownership

- The crawler acquires one page, stores immutable bytes, publishes visit evidence, and advances
  graph work without waiting for catalogue ingestion.
- Ingestor replicas consume one shared durable lane without a leader. Each process owns one NATS
  session and a configurable bounded set of DuckLake writer lanes; each lane has its own catalogue
  connection.
- The materializer scans a pinned `ingest.visits` snapshot into bounded visit batches and maintains
  the complete fixed `material.*` generation, then follows inserted visits through DuckLake CDC.
- The janitor removes only Periplus-owned transient navigation and runtime state.

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

Runtime `web.*` and `content.*` objects belong to a separate lightweight public-catalogue registry.
Its entries reference SQL resources and declare required material relations. Installation fails if
any dependency of the four-relation public contract is missing; runtime SQL is never part of a
materialization declaration.

## Crawl-plan boundary

Crawl plans are editable Periplus Postgres definitions. A run freezes its complete plan configuration
before admitting one or more start URLs into durable crawl requests. Current run, request, and
edge-evaluation state remains in Periplus Postgres. A terminal run schedules its immutable `ingest.crawls`
evidence through the transactional graph outbox and ordinary ingestion path.

Plan edges select URLs only from the navigation package derived from the page
that just completed acquisition. Historical catalogue joins are not an
acquisition-plan capability. URL admission is always deduplicated across the
complete run.

The detailed data contract is in [`SCHEMA.md`](SCHEMA.md), execution and recovery semantics are in
[`LIFECYCLE.md`](LIFECYCLE.md), external loading is in [`IMPORTS.md`](IMPORTS.md), and the portable
query boundary is in [`QUERY.md`](QUERY.md), and plan semantics are in
[`CRAWL_PLANS.md`](CRAWL_PLANS.md).

## Public coverage requests

`coverage_requests` in Periplus Postgres stores public coverage intent, requested depth (0–2),
link scope, and total page budget (1–1,000). Submission saves `pending`; the API's existing
scheduler process automatically picks it up. A request-scoped NATS operation lease suppresses
concurrent processing, and a deterministic run ID makes dispatch retry-stable. No separate
service, delivery queue, or public execution endpoint is introduced.

Python source discovery turns descriptions into at most three distinct Brave search queries,
then selects up to ten returned URLs (also bounded by the page budget). Structured model
outputs select candidate IDs, never invented URLs. Queries and completed searches are
checkpointed; selected URLs are frozen before dispatch. Missing credentials keep requests
pending with a visible explanation; transient discovery errors use bounded backoff and three
attempts. Permanent discovery failures are visible as `failed`.

The ordinary graph runtime executes one run per request with a one-hour deadline. Internal
links are restricted to the registrable starting sites including their subdomains. External-only
links exclude those sites. Both scopes permit any linked site. Depth and the total page budget
apply across all starting pages. Optional `allowed_sections` restrict starting URLs and selected traversal links to explicit HTTP(S) origins and path sections, including descendant paths. Section limits are enforced in Python source selection and ordinary frozen SQL edges; existing runs keep their frozen plans. They do not constrain redirects or subresources. Starting URLs must resolve to public addresses. This initial
DNS check is not an egress sandbox: the CDP service's network boundary must also prevent access
to private networks through redirects, subresources, and DNS rebinding.

The public request API exposes pending, resolving, ongoing, completed, and failed states, source
URLs, search queries, section limits, and safe live run counters. Acquisition-settled counts include failed attempts; acquisition-pending and navigation-pending counts distinguish fetching from outgoing-link work. Completion means collection finished; ingestion
and materialization may still be processing. Failed/cancelled runs remain distinguishable, and
partial page failures remain visible. Request records survive graph-run retention; old run
counters disappear with the runtime record, while the run ID still identifies lake evidence.
Recently completed lists cover the last 30 days. Next.js only proxies the Python endpoints.
