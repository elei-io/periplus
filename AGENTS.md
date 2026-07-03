# Codex Agent Guide

This file is the first stop for Codex agents working in Atlas.

## Project Shape

- Atlas is a Python backend with three entrypoints over shared domain logic:
  - `backend/api/` for FastAPI routes.
  - `backend/cli/` for Typer commands.
  - `backend/domains/` for crawl, index, search, and queue behavior.
- Keep business behavior in `backend/domains/`; API and CLI layers should stay thin.
- The architecture rationale lives in `ARCHITECHTURE.md`. Read it before changing the API/worker/browser execution model.

## Setup

- Use `uv` from the `backend/` directory for Python commands.
- The project targets Python 3.14.
- Docker Compose is the easiest way to run the full API, worker, and NATS stack.
- Runtime cache and generated schema data live under ignored paths: `.cache/`, `.env`, and `backend/.schemas/`.

## Common Commands

From the repository root:

```sh
make sync
make check
make api
make worker
make compose-up
```

Equivalent direct commands:

```sh
cd backend && uv sync
cd backend && uv run python -m compileall api cli domains
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
- Keep Crawl4AI configuration close to the domain service that uses it.
- Avoid introducing a separate browser service unless the architecture document is deliberately updated too.
- Do not commit generated caches, schemas, virtualenvs, or secrets.

## Environment

- `NATS_URL` controls queue access for async index jobs.
- `CACHE_ROOT`, `CACHE_CLEANUP_INTERVAL`, and `CACHE_TTL_SECONDS` control scrape cache storage and cleanup.
- `OPENROUTER_API_KEY` and `OPENROUTER_SEARCH_EXTRACTOR_MODEL` are optional search-schema generation settings.
