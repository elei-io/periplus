# Codex Agent Guide

This file is the first stop for Codex agents working in Atlas.

## Project Shape

- Atlas is a Python backend with three entrypoints over shared domain logic:
  - `backend/api/` for FastAPI routes.
  - `backend/cli/` for Typer commands.
  - `backend/domains/` for crawl, index, search, and queue behavior.
- Current primitives are `search`, `index`, `scrape`, `schema`, and `extract`.
- Page-loading options are shared through `domains.crawl`. Do not duplicate `mode`/`wait` config in individual primitives.
- `scrape` is the page acquisition primitive. It always writes `page.html`, `crawl.json`, and `manifest.json`; `index` should read links from `crawl.json` instead of calling Crawl4AI directly.
- Keep business behavior in `backend/domains/`; API and CLI layers should stay thin.
- The architecture rationale lives in `ARCHITECHTURE.md`. Read it before changing the API/worker/browser execution model.

## Setup

- Use `uv` from the `backend/` directory for Python commands.
- The project targets Python 3.14.
- Docker Compose is the easiest way to run the full API, worker, Postgres, and NATS stack.
- Runtime cache and generated schema data live under ignored paths: `.cache/` and `.env`.

## Common Commands

From the repository root:

```sh
make sync
make check
make api
make worker
cd backend && uv run atlas schema https://example.com --prompt "Extract article cards."
cd backend && uv run atlas extract https://example.com --prompt "Extract the main heading."
cd backend && uv run atlas extract "https://jp.mercari.com/en/search?keyword=16tb%20ironwolf" --mode app --wait stable --prompt "Extract product listings."
make compose-up
```

Equivalent direct commands:

```sh
cd backend && uv sync
cd backend && uv run python -m compileall api cli domains
cd backend && uv run alembic upgrade head
cd backend && uv run alembic check
cd backend && uv run fastapi dev api/app.py
cd backend && uv run python -m domains.index.worker
docker compose up --build
```

## Validation

- Run `make check` after Python edits. It intentionally avoids network-dependent crawling.
- For API changes, prefer adding or running targeted route/service checks before relying on Compose.
- For crawl behavior changes, test at least one low-depth URL and keep concurrency low while debugging.

## Coding Notes

- Preserve the current split: routers and CLI commands validate/input/output; domain modules own behavior.
- Prefer typed Pydantic models for request/response boundaries.
- Use SQLAlchemy 2 models from `domains.database.Base` for database tables and manage schema changes with Alembic.
- Keep shared Crawl4AI browser/run configuration in `domains.crawl`.
- Use `domains.schema` for generated Crawl4AI extraction schemas instead of embedding schema generation in another primitive. Its default cache id is derived from URL domain, prompt hash, schema type, mode, and wait strategy.
- Use `domains.extract` to compose cached HTML from `domains.scrape` with generated schemas from `domains.schema`.
- Do not add optional scrape artifact formats until a real caller needs them.
- Avoid introducing a separate browser service unless the architecture document is deliberately updated too.
- Do not commit generated caches, schemas, virtualenvs, or secrets.

## Environment

- `NATS_URL` controls queue access for async index jobs.
- `DATABASE_URL` controls Postgres access.
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_PORT` configure the local Compose Postgres service.
- `CACHE_ROOT`, `CACHE_CLEANUP_INTERVAL`, and `CACHE_TTL_SECONDS` control generated artifact cache storage and cleanup. Cache entries are organized as `.cache/<domain>/<service_name>/...`.
- `OPENROUTER_API_KEY`, `OPENROUTER_SCHEMA_MODEL`, and `OPENROUTER_SEARCH_EXTRACTOR_MODEL` are optional schema generation settings.
