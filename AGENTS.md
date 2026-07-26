# Working in Atlas

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/CRAWL_GRAPHS.md](docs/CRAWL_GRAPHS.md) before changing graph execution, crawling,
repository storage, DOM generation, NATS, or DuckLake. Read
[docs/HAZARDS.md](docs/HAZARDS.md) before adding a service, queue, persistence path, compatibility
layer, or abstraction. Read [docs/WORKER_ARCHITECTURE.md](docs/WORKER_ARCHITECTURE.md) before
changing worker ownership, queue routing, managed DuckDB use, or deployment scaling.

## Non-negotiable boundaries

- Atlas is 100% greenfield. Do not add backwards-compatibility shims, legacy aliases, dual
  reads/writes, fallback routes, deprecated environment variables, or migration bridges. Change
  the contract directly and delete the superseded path. Prefer resetting disposable development
  state over carrying compatibility code unless the user explicitly requires a real data
  migration.
- Postgres owns editable control state and current graph execution: crawl graphs, runs, requests,
  edge evaluations, admission deduplication, progress counters, schedules, policies, matches,
  schemas, catalogue definitions, and the transactional graph outbox.
- NATS JetStream/KV owns graph work delivery, worker presence, CDC events, operation leases,
  and per-domain crawl pacing/concurrency. It is not authoritative graph state.
- Crawl history belongs only in DuckLake; never reintroduce it into control-plane Postgres.
- Raw HTML is immutable, content-addressed, and stored through `backend/repository/`.
- `crawl` is the only page-acquisition primitive. Graph nodes map admitted URL inputs to crawl work;
  scoped SQL edges derive URL inputs for subsequent nodes from durable crawl evidence.
- Acquisition workers acquire one page, store immutable raw HTML, publish frozen ingestion jobs,
  and own navigation readiness plus outgoing edge evaluation. Branch nodes derive a bounded
  navigation package; leaf nodes skip it. They never wait for catalogue ingestion. Cross-replica
  website concurrency and pacing are keyed per domain; browser and object-store concurrency remain
  bounded by their owning process.
- A standard CDP endpoint is the sole acquisition boundary. Atlas has one crawl queue; the CDP
  service owns transport choice, browser-farm capacity, profiles, and acquisition strategy.
- Ingestion workers own base crawl/DOM/system-projection writes. They are independently observable
  `critical` catalogue work, never settle graph traversal, and never wait for user materialization.
- The CDC ingress consumes Basin-owned global DML/DDL JetStream streams, publishes per-table
  DML ticks and global DDL changes into Atlas JetStream, and ACKs Basin only after Atlas PubAcks.
  Each materialization owns one filtered durable NATS consumer, coalesces ticks, and commits one
  whole-table refresh while retaining a stable target identity. There is no scope queue, coverage
  table, revision fence, fan-out ledger, or separate commit queue.
- An ingestion process owns four independent session-affine DuckBasin clients; a materialization
  process owns eight. Every client is serialized, while different clients run concurrently.
  Bounded client pools provide the normal executor capacity; horizontal replicas are an
  availability and post-saturation scaling control.
- Page-only graph edges use bounded standalone DuckDB connections. Historical edge joins use a
  pinned snapshot through one serialized, read-only catalogue operation per acquisition process.
- DuckBasin owns compaction, old-file cleanup, and physical lake maintenance. Atlas housekeeping
  only reclaims Atlas-owned staging and navigation objects.
- Per-domain crawl permits and operation leases are distinct. Domain permits enforce website
  politeness and leases suppress duplicate durable execution. Do not hold a PostgreSQL advisory
  lock across a remote DuckLake operation.
- DuckBasin owns DuckLake metadata, analytical Parquet layout, compaction, and lake storage. Atlas
  uploads bounded local Arrow/Parquet batches through Quack and does not receive lake S3 credentials.
- Acquisition workers connect to the configured standard CDP endpoint. Atlas owns content correctness,
  including when scrolling is required; the CDP service owns rendering and physical capacity.
- API and CLI code validate and adapt. Graph execution belongs in runtime, acquisition belongs in
  crawl, derived navigation belongs in bounded catalogue SQL, and durable writes belong behind the
  repository boundary.
- JetStream streams, consumers, and KV contracts are reconciled at process startup. Request and
  polling paths reuse process-owned handles and must never call `add_consumer()` or otherwise
  provision infrastructure.

## Code map

- `backend/acquisition/` — standard-CDP page capture, readiness, response classification, and
  acquisition evidence; do not add traversal loops here.
- `backend/control/` — editable Postgres-backed crawl graphs, policies, matches, schemas, and
  catalogue definitions.
- `backend/runtime/` — Postgres-backed graph execution and transactional outbox; NATS work delivery,
  workers, operation leases, and per-domain pacing.
- `backend/workers/` — CDP acquisition, ingestion, materialization, and housekeeping
  process entrypoints.
- `backend/repository/objects/` — immutable content-addressed raw HTML.
- `backend/repository/ingestion/` — repository queue, pipeline, writer, health, and recovery.
- `backend/repository/catalogue/` — DuckBasin connection minter and logical DuckLake boundary.
- `backend/cdc/` — Basin CDC connections, contracts, ingress, metrics, and worker process.
- `backend/repository/service.py` — application-facing durable repository boundary.
- `backend/dom/` — versioned structural DOM projection.
- `backend/api/` and `backend/cli/` — thin adapters.
- `backend/db/` — SQLAlchemy setup and Alembic migrations.
- `packages/atlas-web-shell/` — React frontend.

Keep editable graph and policy definitions under `control/`, current graph execution under
`runtime/`, acquisition behavior in the shared crawl path, navigation in the acquisition worker,
catalogue ingestion under the ingestion worker, user materialization under the materialization
worker, and generic Postgres infrastructure under `backend/db/`. Do not add generic deployment-wide
resource locking; bound clients locally and keep distributed coordination scoped to the exact
domain or operation identity. Do not add a task, action primitive, or action-specific traversal
loop when a node and scoped SQL edge express the behavior.

## Workflow

Use `uv` from `backend/`; the project targets Python 3.14. Common root commands:

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
cd packages/atlas-web-shell
npm run typecheck
npm run build
```

## DuckBasin and Quack upstream

Atlas intentionally uses only the official DuckDB Python package plus Quack for managed DuckLake
access. Do not reintroduce a Basin SDK, `ducklake-client`, local DuckLake attachment configuration,
lake object-store credentials, or Atlas-owned CDC cursors. When Atlas reveals a missing Quack or
DuckBasin primitive, record actionable evidence in [UPSTREAM.md](UPSTREAM.md) and prefer a coherent
upstream fix over an Atlas-only compatibility layer.

## Implementation rules

- Prefer typed Pydantic boundaries and SQLAlchemy 2 models.
- Keep content completion and response handling in the crawl policy boundary. Keep per-domain
  politeness in DomainPolicy. The CDP service owns transport configuration and browser-fleet capacity.
- Keep object keys repository-relative and local filesystem paths out of public contracts.
- Add formats, services, queues, and abstractions only for an active caller.
- Do not model resource acquisition as durable `lock.request`, `lock.acquired`, `lock.release`, or
  generic `work.complete` message chains. Use bounded local pools, and keep the per-domain
  distributed permit inside website politeness.
- Do not commit generated artifacts, local `.atlas/` data, virtual environments, or secrets.
- Manage schema changes with Alembic; do not add compatibility models for removed storage paths.

For the frontend, use shadcn components, React Query for server state, shared API types under
`packages/atlas-web-shell/src/types/`, and named exports except for `App.tsx`. Every mutation must surface
`extractApiError` through `toast.error()`.
