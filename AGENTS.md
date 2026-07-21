# Working in Atlas

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/CRAWL_GRAPHS.md](docs/CRAWL_GRAPHS.md) before changing graph execution, crawling,
repository storage, DOM generation, NATS, or DuckLake. Read
[docs/HAZARDS.md](docs/HAZARDS.md) before adding a service, queue, persistence path, compatibility
layer, or abstraction. Read [docs/WORKER_ARCHITECTURE.md](docs/WORKER_ARCHITECTURE.md) before
changing worker ownership, queue routing, embedded DuckDB use, or deployment scaling.

## Non-negotiable boundaries

- Atlas is 100% greenfield. Do not add backwards-compatibility shims, legacy aliases, dual
  reads/writes, fallback routes, deprecated environment variables, or migration bridges. Change
  the contract directly and delete the superseded path. Prefer resetting disposable development
  state over carrying compatibility code unless the user explicitly requires a real data
  migration.
- Postgres is the control plane: editable crawl graphs, graph nodes and edges, schedules, policies,
  matches, schemas, and catalogue definitions.
- NATS JetStream/KV owns graph runs, crawl requests, queued work, workers, progress, admission,
  deduplication, operation leases, and expiring resource grants.
- Crawl history belongs only in DuckLake; never reintroduce it into control-plane Postgres.
- Raw HTML is immutable, content-addressed, and stored through `backend/repository/`.
- `crawl` is the only page-acquisition primitive. Graph nodes map admitted URL inputs to crawl work;
  scoped SQL edges derive URL inputs for subsequent nodes from durable crawl evidence.
- Acquisition workers acquire one page, store immutable raw HTML, publish frozen ingestion jobs,
  and own navigation readiness plus outgoing edge evaluation. Branch nodes derive a bounded
  navigation package; leaf nodes skip it. They never wait for catalogue ingestion. Deployment-wide
  remote pressure and object-store writes use Resource Governor permits; local browser slots remain
  process-local.
- A standard CDP endpoint is the sole acquisition boundary. Atlas has one crawl queue; the CDP
  service owns transport choice, browser-farm capacity, profiles, and acquisition strategy.
- Ingestion workers own base crawl/DOM/system-projection writes. They are independently observable
  `critical` catalogue work, never settle graph traversal, and never wait for user materialization.
- The catalogue relay publishes per-table DuckLake DML ticks and global DDL changes to JetStream.
  Each materialization owns one filtered durable NATS consumer, coalesces ticks, and commits one
  whole-table refresh while retaining a stable target identity. There is no scope queue, coverage
  table, revision fence, fan-out ledger, or separate commit queue.
- Each ingestion or materialization process owns its embedded DuckDB connection and initially runs
  one catalogue operation at a time. Horizontal replicas provide executor capacity; Resource
  Governor budgets cap combined DuckLake and object-store pressure across replicas.
- Page-only graph edges use bounded standalone DuckDB connections. Historical edge joins use a
  pinned snapshot through one serialized, read-only catalogue operation per acquisition process.
- The maintenance worker performs bounded off-path upkeep under proportional background catalogue
  and object-store permits. It runs concurrently with foreground work, never installs a global
  drain barrier, and does not use a bespoke maintenance-active polling protocol.
- Capacity permits, operation leases, and PostgreSQL advisory commit locks are distinct. Permits
  control pressure, leases suppress duplicate execution, and advisory locks fence correctness.
- The Resource Governor is a narrow admission controller. It never owns work delivery, workflow
  completion, materialization lifecycle, or a generic catalogue RPC surface.
- DuckLake owns analytical Parquet layout and compaction. Do not create permanent per-crawl files.
- Acquisition workers connect to the configured standard CDP endpoint. Atlas owns content correctness,
  including when scrolling is required; the CDP service owns rendering and physical capacity.
- API and CLI code validate and adapt. Graph execution belongs in runtime, acquisition belongs in
  crawl, derived navigation belongs in bounded catalogue SQL, and durable writes belong behind the
  repository boundary.

## Code map

- `backend/actions/` — page acquisition and retained-evidence analysis; do not add navigation
  primitives or traversal loops here.
- `backend/control/` — editable Postgres-backed crawl graphs, policies, matches, schemas, and
  catalogue definitions.
- `backend/runtime/` — NATS-backed graph runs, crawl requests, queues, progress, workers, admission,
  deduplication, operation leases, and resource governance.
- `backend/workers/` — CDP acquisition, ingestion, materialization, and maintenance
  process entrypoints. Resource governance is a shared `runtime/` contract, not a worker service.
- `backend/repository/objects/` — immutable content-addressed raw HTML.
- `backend/repository/ingestion/` — repository queue, pipeline, writer, health, and recovery.
- `backend/repository/catalogue/` — private DuckLake implementation.
- `backend/repository/service.py` — application-facing durable repository boundary.
- `backend/dom/` — versioned structural DOM projection.
- `backend/api/` and `backend/cli/` — thin adapters.
- `backend/db/` — SQLAlchemy setup and Alembic migrations.
- `web/` — React frontend.

Keep editable graph and policy definitions under `control/`, current graph execution under
`runtime/`, acquisition behavior in the shared crawl path, navigation in the acquisition worker,
catalogue ingestion under the ingestion worker, user materialization under the materialization
worker, and generic Postgres infrastructure under `backend/db/`. Resource-allocation algorithms
belong in the governor, not in API adapters or work messages. Do not add a task, action primitive,
or action-specific traversal loop when a node and scoped SQL edge express the behavior.

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
cd web
npm run typecheck
npm run build
```

## Dogfooded DuckLake libraries

Atlas intentionally dogfoods the maintainer's DuckLake libraries:

- `ducklake-client` — Python catalogue client used by the repository; local source at
  `/Users/ekku/Code/quack/ducklake-python-client`.
- `ducklake-cdc` — DuckDB extension providing durable DuckLake change streams; local source at
  `/Users/ekku/Code/quack/ducklake-cdc-extension`.
- `ducklake-cdc-client` — Python CDC client intended for publication consumers; local source at
  `/Users/ekku/Code/quack/ducklake-cdc-python-client`.

Treat these as actively maintained upstreams, not immutable third-party constraints. When Atlas
reveals a missing primitive, awkward API, correctness risk, performance problem, or documentation
gap, prefer a small coherent upstream improvement over an Atlas-only wrapper, workaround, or copied
implementation. Record actionable findings in [UPSTREAM.md](UPSTREAM.md), including evidence and the
Atlas use case. The maintainer expects candid feedback and can publish updated packages quickly.

Do not silently depend on unpublished local upstream changes. Unless the user explicitly asks for
cross-repository work, change only Atlas and report the upstream need. After an upstream release,
consume its published package normally, update the lockfile, and remove any temporary Atlas code
that the released capability supersedes.

## Implementation rules

- Prefer typed Pydantic boundaries and SQLAlchemy 2 models.
- Keep content completion and response handling in the crawl policy boundary. Keep per-domain
  politeness in DomainPolicy. The CDP service owns transport configuration and browser-fleet capacity.
- Keep object keys repository-relative and local filesystem paths out of public contracts.
- Add formats, services, queues, and abstractions only for an active caller.
- Do not model resource acquisition as durable `lock.request`, `lock.acquired`, `lock.release`, or
  generic `work.complete` message chains. A worker retains its durable work message while waiting
  for an atomic, expiring permit bundle.
- Keep scheduling classes fixed and code-owned: `critical`, `live`, `backfill`, and `maintenance`.
  Deployment configuration controls capacities and weights, not arbitrary scheduling programs.
- Do not commit generated artifacts, local `.atlas/` data, virtual environments, or secrets.
- Manage schema changes with Alembic; do not add compatibility models for removed storage paths.

For the frontend, use shadcn components, React Query for server state, shared API types under
`web/src/types/`, and named exports except for `App.tsx`. Every mutation must surface
`extractApiError` through `toast.error()`.
