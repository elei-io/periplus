# Working in Atlas

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing task execution, crawling,
repository storage, DOM generation, NATS, or DuckLake. Read
[docs/HAZARDS.md](docs/HAZARDS.md) before adding a service, queue, persistence path, compatibility
layer, or abstraction.

## Non-negotiable boundaries

- Atlas is 100% greenfield. Do not add backwards-compatibility shims, legacy aliases, dual
  reads/writes, fallback routes, deprecated environment variables, or migration bridges. Change
  the contract directly and delete the superseded path. Prefer resetting disposable development
  state over carrying compatibility code unless the user explicitly requires a real data
  migration.
- Postgres is the control plane: editable tasks, schedules, policies, matches, and schemas.
- NATS JetStream/KV owns queued work and current task-run, worker, progress, and ingestion state.
- Crawl history belongs only in DuckLake; never reintroduce it into control-plane Postgres.
- Raw HTML is immutable, content-addressed, and stored through `backend/repository/`.
- `crawl` is the only page-acquisition chokepoint. Other actions compose it.
- Runtime workers publish frozen ingestion jobs; only the repository worker writes DuckLake.
- DuckLake owns analytical Parquet layout and compaction. Do not create permanent per-crawl files.
- Browser concurrency is bounded per worker; replica count determines deployment-wide capacity.
- API and CLI code validate and adapt. Business behavior belongs in actions, tasks, and repository
  modules.

## Code map

- `backend/actions/` — search, index, crawl, extract, calibration, and shared action behavior.
- `backend/control/` — editable Postgres-backed tasks, policies, matches, and schemas.
- `backend/runtime/` — NATS-backed runs, queues, progress, workers, and crawl capacity.
- `backend/repository/objects/` — immutable content-addressed raw HTML.
- `backend/repository/ingestion/` — repository queue, pipeline, writer, health, and recovery.
- `backend/repository/catalogue/` — private DuckLake implementation.
- `backend/repository/service.py` — application-facing durable repository boundary.
- `backend/dom/` — versioned structural DOM projection.
- `backend/api/` and `backend/cli/` — thin adapters.
- `backend/db/` — SQLAlchemy setup and Alembic migrations.
- `web/` — React frontend.

Keep action request/response models in `actions/<name>/schemas.py`. Keep editable definitions under
`control/`, current execution under `runtime/`, and generic Postgres infrastructure under
`backend/db/`.

## Workflow

Use `uv` from `backend/`; the project targets Python 3.14. Common root commands:

```sh
make sync
make check
make api
make db-upgrade
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

## Implementation rules

- Prefer typed Pydantic boundaries and SQLAlchemy 2 models.
- Share page-loading configuration through `actions.shared.crawl`; do not duplicate mode/wait
  behavior in actions.
- Keep object keys repository-relative and local filesystem paths out of public contracts.
- Add formats, services, queues, and abstractions only for an active caller.
- Do not commit generated artifacts, local `.atlas/` data, virtual environments, or secrets.
- Manage schema changes with Alembic; do not add compatibility models for removed storage paths.

For the frontend, use shadcn components, React Query for server state, shared API types under
`web/src/types/`, and named exports except for `App.tsx`. Every mutation must surface
`extractApiError` through `toast.error()`.
