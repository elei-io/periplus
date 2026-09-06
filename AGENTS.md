# Working in Periplus

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/SCHEMA.md](docs/SCHEMA.md), and
[docs/LIFECYCLE.md](docs/LIFECYCLE.md) before changing graph execution, crawling, worker
ownership, repository storage, DOM generation, NATS, DuckLake, or managed DuckDB use. Read
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) before changing service names, process roles, deployment
topology, environment contracts, container scaling, or infrastructure ownership. Read
[docs/CUTOFF.md](docs/CUTOFF.md) before adding a service, queue, persistence path,
compatibility layer, or abstraction. Read [docs/QUERY.md](docs/QUERY.md) before changing
`web.*`, `content.*`, SDK, or DuckDB extension boundaries. Read
[docs/EXTENSION_DEVELOPMENT.md](docs/EXTENSION_DEVELOPMENT.md) before building, testing, or
changing the DuckLake CDC extension.

For every query performance issue, first classify it as schema/catalogue design, compiler/optimizer
behavior, or both, using the evidence and decision rules in [docs/QUERY.md](docs/QUERY.md).
Compiler actions include warnings, boundedness errors, and semantics-preserving SQL or plan
rewrites. Do not use a rewrite to conceal a poor public schema, and do not change the schema merely
to encode around one accidental optimizer plan.

## Non-negotiable boundaries

- Periplus is 100% greenfield. Do not add backwards-compatibility shims, legacy aliases, dual
  reads/writes, fallback routes, deprecated environment variables, or migration bridges. Change
  the contract directly and delete the superseded path. Prefer resetting disposable development
  state over carrying compatibility code unless the user explicitly requires a real data
  migration.
- Periplus Postgres owns editable control state and current graph execution: crawl plans, runs, requests,
  edge evaluations, admission deduplication, progress counters, schedules, policies, matches,
  schemas, catalogue definitions, and the transactional graph outbox.
- NATS JetStream/KV owns graph, ingestion, and materialization work delivery, worker presence,
  operation leases, and per-domain crawl pacing/concurrency. It is not authoritative graph state.
- Crawl history belongs only in DuckLake; never reintroduce it into Periplus Postgres.
- Raw HTML is immutable, content-addressed, and stored through
  `packages/periplus/src/periplus/ingestion/objects/`.
- `crawl` is the only page-acquisition primitive. Graph nodes map admitted URL inputs to crawl work;
  scoped SQL edges derive URL inputs for subsequent nodes from durable crawl evidence.
- Crawler replicas acquire one page, store immutable raw HTML, publish frozen ingestion jobs,
  and own navigation readiness plus outgoing edge evaluation. Branch nodes derive a bounded
  navigation package; leaf nodes skip it. They never wait for catalogue ingestion. Cross-replica
  website concurrency and pacing are keyed per domain; browser and object-store concurrency remain
  bounded by their owning process.
- A standard CDP endpoint is the sole acquisition boundary. Periplus has one crawl queue; the CDP
  service owns transport choice, browser-farm capacity, profiles, and acquisition strategy.
- Ingestor replicas own base crawl evidence writes. Replicas are symmetric consumers of one
  durable lane: each process owns one NATS session and bounded configurable writer lanes, each
  with an independent DuckLake connection. They are independently observable `critical`
  catalogue work, never settle graph traversal, and never wait for user materialization.
- Complete materialization rebuilds use one visit-scoped workload. Every non-private module under
  `materialization/projections/` is one self-contained, auto-discovered projection declaration;
  adding, editing, or deleting a materialization touches only that file before redeploy and rebuild.
  Public views and macros belong to the separate public-catalogue registry. A planner pins a source
  snapshot and publishes bounded visit-ID batches to JetStream. Horizontally scalable materializers
  build one shared parse context, register final Parquet using each projection's partition policy,
  record the applied batch in the same DuckLake transaction, and ACK only after commit. All
  discovered relations belong to one
  registry-digested hidden generation, catch up inserted visits to a source high-water mark, and
  activate atomically. After activation, one insert-only DuckLake CDC consumer publishes
  deterministic visit batches to the same JetStream lane. Every materializer replica is an
  eligible coordinator; a NATS operation lease elects one connection, which then holds the
  DuckLake consumer's owner-token lease. No worker is statically designated and the durable cursor
  remains only in DuckLake. The CDC cursor advances only after all applied markers are durable.
- Crawl-plan edges use bounded standalone DuckDB connections over the current
  page's navigation package. Historical catalogue joins are not a plan-edge capability.
- LakeDucktor owns compaction, old-file cleanup, and physical lake maintenance. The Periplus janitor
  only reclaims Periplus-owned staging and navigation objects.
- Per-domain crawl permits and operation leases are distinct. Domain permits enforce website
  politeness and leases suppress duplicate durable execution. Do not hold a PostgreSQL advisory
  lock across a remote DuckLake operation.
- Periplus attaches its own DuckLake directly through the official DuckDB extensions and owns logical
  table layout and generation transactions. LakeDucktor owns physical lake maintenance.
- Crawler replicas connect to the configured standard CDP endpoint. Periplus owns content correctness,
  including when scrolling is required; the CDP service owns rendering and physical capacity.
- API and CLI code validate and adapt. Graph execution belongs in runtime, acquisition belongs in
  crawl, derived navigation belongs in bounded catalogue SQL, and durable writes belong behind the
  repository boundary.
- JetStream streams, consumers, and KV contracts are reconciled at process startup. Request and
  polling paths reuse process-owned handles and must never call `add_consumer()` or otherwise
  provision infrastructure.

## Code map

- `packages/periplus/src/periplus/crawl/control/` — editable Periplus Postgres-backed crawl graphs, policies, and
  schedules.
- `packages/periplus/src/periplus/crawl/runtime/` — current graph execution, transactional outbox, work
  delivery, navigation, progress, and per-domain pacing.
- `packages/periplus/src/periplus/crawl/acquisition/` — standard-CDP page capture, readiness, response
  classification, and acquisition evidence; do not add traversal loops here.
- `packages/periplus/src/periplus/crawl/crawler.py` — crawler process composition.
- `packages/periplus/src/periplus/ingestion/` — immutable objects, ingestion contracts, queue, writer, import,
  health, and recovery.
- `packages/periplus/src/periplus/ingestion/ingestor.py` — ingestor process composition.
- `packages/periplus/src/periplus/materialization/` — fixed document and visit projections, maintenance, and
  materialization lifecycle.
- `packages/periplus/src/periplus/materialization/materializer.py` — materializer process composition.
- `packages/periplus/src/periplus/materialization/dom/` — versioned structural DOM projection.
- `packages/periplus/src/periplus/query/` — bounded physical SQL inspection.
- `packages/periplus/src/periplus/operations/janitor.py` — janitor process composition.
- `packages/periplus/src/periplus/operations/api/` — status and dead-letter operations.
- `packages/periplus/src/periplus/platform/` — configuration, worker health/lifecycle, and Postgres, NATS, and
  DuckLake adapters; business workflows do not belong here.
- `packages/periplus/src/periplus/entrypoints/` — thin API, worker CLI, and setup composition roots.
- `packages/periplus-web-shell/` — distributable browser SQL shell built on `periplus-console-core`.
- `packages/periplus-public/` — Next.js public catalogue and crawl-submission application.
- `packages/periplus-admin/` — Vite operator application for crawl and catalogue maintenance.

Keep editable graph and policy definitions under `crawl/control/`, current graph execution under
`crawl/runtime/`, acquisition behavior in the shared crawl path, durable evidence under
`ingestion/`, fixed projections under `materialization/`, and generic adapters under `platform/`.
Entrypoints validate, compose, and run these capabilities; they do not own domain transitions. Do
not add generic deployment-wide resource locking; bound clients locally and keep distributed
coordination scoped to the exact domain or operation identity. Do not add a task, action primitive,
or action-specific traversal loop when a node and scoped SQL edge express the behavior.

## Workflow

Use `uv` from `packages/periplus/`; the project targets Python 3.14. Common root commands:

```sh
make sync
make check
make api
make setup
make catalogue-check
make compose-up
```

Run `make check` after Python changes. Add targeted tests for changed behavior. For crawl changes,
exercise one low-depth URL with low concurrency. For frontend changes, run:

```sh
cd packages/periplus-public
npm run typecheck
npm run build
cd ../periplus-admin
npm run typecheck
npm run build
```

### DuckLake CDC extension

Periplus uses standard DuckDB and has no custom query extension. Query validation and future
optimizations belong behind the query API. The separately maintained DuckLake CDC extension is
loaded only by live materialization; keep its source and DuckDB ABI pinned together.
See [docs/EXTENSION_DEVELOPMENT.md](docs/EXTENSION_DEVELOPMENT.md) for that boundary.

`./ducklake.sh` opens the standard DuckDB CLI on PATH with the configured lake attached read-only.
Pass `--sql "..."` to execute a statement and exit. No sibling query extension build is required.

## DuckLake upstream

Periplus runtime code uses the official DuckDB `ducklake` and metadata-store extensions directly.
The pinned DuckLake CDC extension is loaded only by the dedicated live-materialization connection;
it owns the durable `ingest.visits` cursor while JetStream remains delivery only. Do not introduce
a Basin SDK, Quack transport, compatibility attachment, or second Periplus-owned incremental cursor.
When Periplus reveals a missing DuckLake primitive, record actionable evidence in
[UPSTREAM.md](UPSTREAM.md) and prefer a coherent upstream fix over a Periplus-only compatibility
layer.

## Implementation rules

- Prefer typed Pydantic boundaries and SQLAlchemy 2 models.
- Keep content completion and response handling in the content policy boundary. Keep per-domain
  politeness in DomainPolicy. The CDP service owns transport configuration and browser-fleet capacity.
- Keep object keys repository-relative and local filesystem paths out of public contracts.
- Add formats, services, queues, and abstractions only for an active caller.
- Do not model resource acquisition as durable `lock.request`, `lock.acquired`, `lock.release`, or
  generic `work.complete` message chains. Use bounded local pools, and keep the per-domain
  distributed permit inside website politeness.
- Do not commit generated artifacts, local `.periplus/` data, virtual environments, or secrets.
- Manage schema changes with Alembic; do not add compatibility models for removed storage paths.

Before changing either frontend, read its package-local `AGENTS.md` in full.

For the frontend, use shadcn components, React Query for server state, shared API types under
each application’s `src/types/`, and named exports except for `App.tsx`. Every mutation must surface
`extractApiError` through `toast.error()`.
