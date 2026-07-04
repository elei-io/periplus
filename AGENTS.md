# Codex Agent Guide

This file is the first stop for Codex agents working in Atlas.

## Project Shape

- Atlas is a Python backend with three entrypoints over shared action/task logic:
  - `backend/api/` for FastAPI routes.
  - `backend/cli/` for Typer commands.
  - `backend/actions/` for primitive action behavior.
  - `backend/tasks/` for persisted schedulable work and task/effect runs.
  - `backend/shared/` for crawler, artifacts, quality, progress, and extraction-schema support.
- Current user-facing actions are `search`, `index`, `scrape`, and `extract`. Extraction schema generation lives in `shared.extract_schema`.
- Page-loading options are shared through `shared.crawl`. Do not duplicate `mode`/`wait` config in individual primitives.
- `scrape` is the page acquisition primitive. It always writes `page.html`, `crawl.json`, and `manifest.json`; `index` should read links from `crawl.json` instead of calling Crawl4AI directly.
- Keep business behavior in `backend/actions/`, `backend/tasks/`, and `backend/shared/`; API and CLI layers should stay thin.
- The architecture rationale lives in `ARCHITECHTURE.md`. Read it before changing the API/task/browser execution model.

## Setup

- Use `uv` from the `backend/` directory for Python commands.
- The project targets Python 3.14.
- Docker Compose is the easiest way to run the local API and Postgres stack.
- Runtime cache and generated schema data live under ignored paths: `.cache/` and `.env`.

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
cd backend && uv run python -m compileall actions api cli db shared tasks
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

- Preserve the current split: routers and CLI commands validate/input/output; action/task/shared modules own behavior.
- Prefer typed Pydantic models for request/response boundaries.
- Keep Postgres infrastructure under `backend/db`: SQLAlchemy base/session setup, model registry, and Alembic files.
- Keep SQLAlchemy task tables in `tasks/models.py` and Pydantic contracts in `tasks/schemas.py`.
- Keep action Pydantic contracts in `actions/<name>/schemas.py`.
- Use SQLAlchemy 2 models from `db.Base` for database tables and manage schema changes with Alembic.
- Keep shared Crawl4AI browser/run configuration in `shared.crawl`.
- Use `shared.extract_schema` for generated Crawl4AI extraction schemas instead of embedding schema generation in another primitive. Its default cache id is derived from URL domain, prompt hash, schema type, mode, and wait strategy.
- Use `actions.extract` to compose cached HTML from `actions.scrape` with generated schemas from `shared.extract_schema`.
- Do not add optional scrape artifact formats until a real caller needs them.
- Avoid introducing a separate browser service or per-action job worker unless the architecture document is deliberately updated too.
- Do not commit generated caches, schemas, virtualenvs, or secrets.

## Environment

- `DATABASE_URL` controls Postgres access.
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_PORT` configure the local Compose Postgres service.
- `CACHE_ROOT`, `CACHE_CLEANUP_INTERVAL`, and `CACHE_TTL_SECONDS` control generated artifact cache storage and cleanup. Cache entries are organized as `.cache/<domain>/<service_name>/...`.
- `OPENROUTER_API_KEY`, `OPENROUTER_SCHEMA_MODEL`, and `OPENROUTER_SEARCH_EXTRACTOR_MODEL` are optional schema generation settings.
