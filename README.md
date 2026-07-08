# Atlas

Atlas is a Python web crawling and search backend. It exposes the same action logic through:

- a FastAPI HTTP API,
- a Typer CLI.

Its current user-facing web actions are `search`, `index`, `crawl`, and `extract`.
Extraction schema generation is shared support used by `search` and `extract`.

The current architecture is documented in [ARCHITECHTURE.md](ARCHITECHTURE.md).

## Repository Layout

```text
backend/api/          FastAPI app and routers
backend/cli/          Typer CLI commands
backend/actions/      Primitive actions that take inputs and produce outputs
backend/actions/shared/
                      Crawler, quality, progress, and extraction-schema support
backend/artifacts/    Ephemeral task-run artifact metadata and disk helpers
backend/tasks/        Persisted schedulable work, effects, and task runs
backend/db/           Postgres setup, SQLAlchemy base/session, and Alembic
docker/atlas/         Backend container image
docker-compose.yml    Local API and Postgres stack
```

## Requirements

- Docker and Docker Compose for the full local stack.
- `uv` for local backend development.
- Python 3.14, matching `backend/pyproject.toml`.

## Local Development

Install backend dependencies:

```sh
cd backend
uv sync
```

Run the API locally:

```sh
cd backend
uv run fastapi dev api/app.py
```

Run the CLI:

```sh
cd backend
uv run atlas --help
uv run atlas crawl https://example.com
uv run atlas index https://example.com --max-depth 1
uv run atlas schema https://example.com --prompt "Extract article cards with title and URL."
uv run atlas extract https://example.com --prompt "Extract the main heading and visible links."
uv run atlas extract "https://jp.mercari.com/en/search?keyword=16tb%20ironwolf" \
  --mode app --wait stable \
  --prompt "Extract each product listing with name, availability status, and price."
```

Run a lightweight syntax check:

```sh
make check
```

Run database migrations:

```sh
make db-upgrade
cd backend && uv run alembic -c db/alembic.ini current
```

## Docker Compose

Start the API and Postgres:

```sh
docker compose up --build
```

The API is published at `http://127.0.0.1:8000`. Postgres is published at
`127.0.0.1:5432` by default and persists data in the `atlas-postgres-data`
Compose volume.

## Configuration

Copy `.env.example` to `.env` when local secrets are needed.

`DATABASE_URL` points Atlas at Postgres. Compose injects an internal URL for
the API container using `atlas-postgres`; local tools can use the localhost URL
from `.env.example`.

SQLAlchemy models should inherit from `db.Base`. Task-owned tables live in
`tasks/models.py`, while Pydantic contracts live in `tasks/schemas.py`.
Action inputs/outputs live in `actions/<name>/schemas.py`. Alembic reads model metadata from
`backend/db/alembic/env.py`, so create schema changes with:

```sh
make db-revision m="describe change"
make db-upgrade
cd backend && uv run alembic -c db/alembic.ini check
```

`OPENROUTER_API_KEY` is only needed when Atlas must generate an extraction schema or infer a target JSON example. Page-load-sensitive commands support `--mode` and `--wait`; use `--mode app --wait stable` for pages that need frontend hydration before their data appears.

Sync CLI/API actions return their data directly and do not write local artifacts. Managed task runs will write ephemeral byte artifacts under `.artifacts/`:

```text
.artifacts/task-runs/<task-run-id>/result.html
.artifacts/task-runs/<task-run-id>/result.json
.artifacts/task-runs/<task-run-id>/atlas.json
```

Artifact metadata lives in Postgres through lightweight `artifacts` rows. The bytes on disk are ephemeral and may later be uploaded to S3 as the durable result layer.
