# Codex Agent Guide

This file is the first stop for Codex agents working in Atlas.

The storage and state-ownership contract lives in `STORAGE.md`. Read it completely before changing
crawls, task/effect runs, repository storage, DOM/Parquet generation, or DuckLake. Crawl history
must never be reintroduced into the Atlas control-plane Postgres database.

## Project Shape

- Atlas is a Python backend with three entrypoints over shared action/task logic:
  - `backend/api/` for FastAPI routes.
  - `backend/cli/` for Typer commands.
  - `backend/actions/` for primitive action behavior.
  - `backend/actions/shared/` for crawler, quality, progress, data-schema, and query-schema support shared between actions.
  - `backend/urls/`, `backend/crawl_policies/`, `backend/data_schemas/`, and `backend/query_schemas/` for user-editable matching, policy, and schema state.
  - `backend/tasks/` for persisted schedulable task/effect definitions and the current run implementation; target executions live in JetStream.
  - `backend/repository/ducklake/` owns the DuckLake implementation, schema contract, and bootstrap boundary.
  - `backend/dom/` owns the versioned element schema, bounded Arrow encoder, page-local reader, and structural query helpers.
  - `backend/repository/` owns raw object storage, the JetStream ingestion queue/writer, and repository maintenance around the catalogue.
- Current user-facing actions are `search`, `index`, `crawl`, and `extract`. Data schema generation lives in `actions.shared.data_schema`; query parameter schema generation lives in `actions.shared.query_schema` and is exposed through `extract`.
- Page-loading options are shared through `actions.shared.crawl`. Do not duplicate `mode`/`wait` config in individual primitives.
- `crawl` is the page acquisition chokepoint. The target stores canonical content-addressed HTML in the configured filesystem/S3 repository and durable crawl/document/element state in DuckLake.
- Keep business behavior in `backend/actions/`, `backend/tasks/`, `backend/dom/`, and `backend/repository/`; API and CLI layers should stay thin.
- The architecture rationale lives in `ARCHITECHTURE.md`; storage detail lives in `STORAGE.md`. Read both before changing the API/task/browser/storage execution model.

## Setup

- Use `uv` from the `backend/` directory for Python commands.
- The project targets Python 3.14.
- Docker Compose is the easiest way to run the local API, worker, Postgres, and NATS stack.
- Local repository and staging paths under `.atlas/` are ignored. `.env` is ignored.

## Common Commands

From the repository root:

```sh
make sync
make check
make api
cd backend && uv run atlas schema https://example.com --prompt "Extract article cards."
cd backend && uv run atlas extract https://example.com --prompt "Extract the main heading."
cd backend && uv run atlas extract "https://jp.mercari.com/en/search?keyword=16tb%20ironwolf" --mode app --wait stable --prompt "Extract product listings."
make compose-up
```

Equivalent direct commands:

```sh
cd backend && uv sync
cd backend && uv run python -m compileall actions api catalogue cli db dom repository tasks urls data_schemas query_schemas crawl_policies
cd backend && uv run alembic -c db/alembic.ini upgrade head
cd backend && uv run alembic -c db/alembic.ini check
cd backend && uv run fastapi dev api/app.py
docker compose up --build
```

## Validation

- Run `make check` after Python edits. It intentionally avoids network-dependent crawling.
- For API changes, prefer adding or running targeted route/service checks before relying on Compose.
- For crawl behavior changes, test at least one low-depth URL and keep concurrency low while debugging.

## Coding Notes

- Routers and CLI commands validate/input/output; action, task, DOM, and repository modules own behavior.
- Prefer typed Pydantic models for request/response boundaries.
- Keep Postgres infrastructure under `backend/db`: SQLAlchemy base/session setup, model registry, and Alembic files.
- Keep user-editable SQLAlchemy task/effect definitions in `tasks/models.py` and Pydantic contracts in `tasks/schemas.py`. Do not add new Postgres task/effect run dependencies; target executions belong in JetStream.
- The Postgres artifact/crawl/URL-history models are gone. Do not add compatibility models or routes; use DuckLake documents/crawls/elements according to `STORAGE.md`.
- Keep action Pydantic contracts in `actions/<name>/schemas.py`.
- Use SQLAlchemy 2 models from `db.Base` for database tables and manage schema changes with Alembic.
- Keep shared Crawl4AI browser/run configuration in `actions.shared.crawl`.
- Use durable `data_schemas` records for generated Crawl4AI data schemas instead of embedding schema generation in another primitive. Schema reuse is controlled by explicit URL match patterns.
- Use `actions.extract` to compose HTML from `actions.crawl` with generated schemas from `actions.shared.data_schema` and `actions.shared.query_schema`. The extract primitive can run data extraction, query-parameter extraction, or both.
- Do not add optional crawl media formats until a real caller needs them. Raw HTML.zst and the versioned DOM representation are the initial durable formats.
- DuckLake owns physical catalog Parquet paths and compaction output. Never create a permanent Parquet file per crawl in DuckLake's data path.
- Store repository object keys relative to the configured filesystem/S3 root; never expose local paths in public API contracts.
- Avoid introducing a separate browser service or per-action job worker unless the architecture document is deliberately updated too.
- Do not commit generated artifacts, schemas, virtualenvs, or secrets.

## Frontend Notes

- Use shadcn UI components for frontend UI; do not build custom UI when a shadcn component is available.
- Add missing shadcn components from `web/` with `npx shadcn@latest add <component>`. If that command fails, stop and ask the user to install the component.
- Use React Query (`@tanstack/react-query`) for backend fetching. Do not use `useEffect` for backend data fetching.
- Move repeated data fetching, mutation, and view logic into reusable hooks so component files stay small.
- Keep components small and composed from reusable, well-designed pieces. Avoid duplicate component or hook logic.
- Every `useMutation` call must define an `onError` handler that shows `toast.error()` with an extracted API error message.
- Use `extractApiError` from `web/src/lib/api.ts` for mutation error messages.
- Put shared types and interfaces in `web/src/types/<domain>.ts`. Component-local `Props` types are fine, but API response shapes must not be defined inline.
- Use named exports only: `export function` or `export const`. Do not use `export default` except in `web/src/App.tsx`.

## Environment

- `DATABASE_URL` controls Postgres access.
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_PORT` configure the local Compose Postgres service.
- `ATLAS_CATALOGUE_*` configures DuckLake metadata and its local DuckDB runtime. PostgreSQL is the default metadata catalogue and uses a separate `atlas_catalogue` database.
- `ATLAS_REPOSITORY_STORAGE=disk|s3` and the other `ATLAS_REPOSITORY_*` variables configure both raw-object storage and DuckLake data storage; do not introduce a second storage-backend selection.
- `ATLAS_INGEST_*` configures the dedicated repository writer's batching, redelivery, and stale-staging cleanup. Crawl workers must not write DuckLake directly.
- `OPENROUTER_API_KEY`, `OPENROUTER_SCHEMA_MODEL`, and `OPENROUTER_SEARCH_EXTRACTOR_MODEL` are optional schema generation settings.
