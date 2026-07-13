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
  deduplication, and ingestion state.
- Crawl history belongs only in DuckLake; never reintroduce it into control-plane Postgres.
- Raw HTML is immutable, content-addressed, and stored through `backend/repository/`.
- `crawl` is the only page-acquisition primitive. Graph nodes map admitted URL inputs to crawl work;
  scoped SQL edges derive URL inputs for subsequent nodes from durable crawl evidence.
- Acquisition workers only acquire one page, store immutable raw HTML, and publish frozen ingestion
  jobs. They never open DuckLake or wait for downstream work.
- HTTP, browser, and external-provider acquisition are separate queue and deployment scaling
  dimensions with one shared raw-HTML output contract.
- Ingestion workers own base crawl/DOM/system-projection writes, navigation readiness, and outgoing
  edge evaluation. They never wait for user materialization.
- Materialization workers independently own CDC discovery, backfill, bounded scope evaluation,
  live-view commits, coverage, dematerialization, and lag settlement.
- Each ingestion or materialization process owns its embedded DuckDB connection and initially runs
  one catalogue operation at a time. Horizontal replicas provide concurrency.
- The maintenance worker performs leased off-path upkeep and consumes no hot-path worker capacity.
- DuckLake owns analytical Parquet layout and compaction. Do not create permanent per-crawl files.
- Browser workers own one long-lived Chromium runtime with bounded local page concurrency; browser
  replicas determine physical browser capacity independently from HTTP acquisition.
- API and CLI code validate and adapt. Graph execution belongs in runtime, acquisition belongs in
  crawl, derived navigation belongs in bounded catalogue SQL, and durable writes belong behind the
  repository boundary.

## Code map

- `backend/actions/` — page acquisition and retained-evidence analysis during the graph cutover;
  do not add new navigation primitives here.
- `backend/control/` — editable Postgres-backed crawl graphs, policies, matches, schemas, and
  catalogue definitions.
- `backend/runtime/` — NATS-backed graph runs, crawl requests, queues, progress, workers, admission,
  deduplication, maintenance delivery, and crawl capacity.
- `backend/workers/` — transport-specific acquisition, ingestion, materialization, and maintenance
  process entrypoints.
- `backend/repository/objects/` — immutable content-addressed raw HTML.
- `backend/repository/ingestion/` — repository queue, pipeline, writer, health, and recovery.
- `backend/repository/catalogue/` — private DuckLake implementation.
- `backend/repository/service.py` — application-facing durable repository boundary.
- `backend/dom/` — versioned structural DOM projection.
- `backend/api/` and `backend/cli/` — thin adapters.
- `backend/db/` — SQLAlchemy setup and Alembic migrations.
- `web/` — React frontend.

Keep editable graph and policy definitions under `control/`, current graph execution under
`runtime/`, acquisition behavior in the shared crawl path, ingestion/navigation under the
ingestion worker, user materialization under the materialization worker, and generic Postgres
infrastructure under `backend/db/`. Do not add a task, action primitive, or action-specific
traversal loop when a node and scoped SQL edge express the behavior.

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
- Share page-loading configuration through the crawl acquisition boundary; do not duplicate
  mode/wait behavior in graph execution or edge evaluation.
- Keep object keys repository-relative and local filesystem paths out of public contracts.
- Add formats, services, queues, and abstractions only for an active caller.
- Do not commit generated artifacts, local `.atlas/` data, virtual environments, or secrets.
- Manage schema changes with Alembic; do not add compatibility models for removed storage paths.

For the frontend, use shadcn components, React Query for server state, shared API types under
`web/src/types/`, and named exports except for `App.tsx`. Every mutation must surface
`extractApiError` through `toast.error()`.
