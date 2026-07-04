# Codex Agent Guide

This file is the first stop for Codex agents working in Atlas.

## Project Shape

- Atlas is a Python backend with three entrypoints over shared action/task logic:
  - `backend/api/` for FastAPI routes.
  - `backend/cli/` for Typer commands.
  - `backend/actions/` for primitive action behavior.
  - `backend/actions/shared/` for crawler, quality, progress, and extraction-schema support shared between actions.
  - `backend/artifacts/` for ephemeral task-run artifact metadata and disk helpers.
  - `backend/tasks/` for persisted schedulable work and task/effect runs.
- Current user-facing actions are `search`, `index`, `scrape`, and `extract`. Extraction schema generation lives in `actions.shared.extract_schema`.
- Page-loading options are shared through `actions.shared.crawl`. Do not duplicate `mode`/`wait` config in individual primitives.
- `scrape` is the page acquisition primitive. Sync actions return acquired HTML, Crawl4AI JSON, and warnings in memory; task execution is responsible for writing `result.html`, `result.json`, and `atlas.json` artifacts.
- Keep business behavior in `backend/actions/`, `backend/artifacts/`, and `backend/tasks/`; API and CLI layers should stay thin.
- The architecture rationale lives in `ARCHITECHTURE.md`. Read it before changing the API/task/browser execution model.

## Setup

- Use `uv` from the `backend/` directory for Python commands.
- The project targets Python 3.14.
- Docker Compose is the easiest way to run the local API and Postgres stack.
- Ephemeral task-run artifacts live under ignored paths: `.artifacts/` and `.env`.

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
cd backend && uv run python -m compileall actions artifacts api cli db tasks
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

- Preserve the current split: routers and CLI commands validate/input/output; action, artifact, and task modules own behavior.
- Prefer typed Pydantic models for request/response boundaries.
- Keep Postgres infrastructure under `backend/db`: SQLAlchemy base/session setup, model registry, and Alembic files.
- Keep SQLAlchemy task tables in `tasks/models.py` and Pydantic contracts in `tasks/schemas.py`.
- Keep SQLAlchemy artifact tables in `artifacts/models.py` and Pydantic contracts in `artifacts/schemas.py`.
- Keep action Pydantic contracts in `actions/<name>/schemas.py`.
- Use SQLAlchemy 2 models from `db.Base` for database tables and manage schema changes with Alembic.
- Keep shared Crawl4AI browser/run configuration in `actions.shared.crawl`.
- Use `actions.shared.extract_schema` for generated Crawl4AI extraction schemas instead of embedding schema generation in another primitive. Its default schema id is derived from URL domain, prompt hash, schema type, mode, and wait strategy.
- Use `actions.extract` to compose HTML from `actions.scrape` with generated schemas from `actions.shared.extract_schema`.
- Do not add optional scrape artifact formats until a real caller needs them.
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
- `ARTIFACTS_ROOT`, `ARTIFACTS_CLEANUP_INTERVAL`, and `ARTIFACTS_TTL_SECONDS` control ephemeral local task-run artifact storage and cleanup.
- `OPENROUTER_API_KEY`, `OPENROUTER_SCHEMA_MODEL`, and `OPENROUTER_SEARCH_EXTRACTOR_MODEL` are optional schema generation settings.
