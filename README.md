# Atlas

Atlas is a Python web crawling and search backend. It exposes the same domain logic through:

- a FastAPI HTTP API,
- a Typer CLI,
- an async NATS JetStream worker for index jobs.

The current architecture is documented in [ARCHITECHTURE.md](ARCHITECHTURE.md).

## Repository Layout

```text
backend/api/          FastAPI app and routers
backend/cli/          Typer CLI commands
backend/domains/      Shared crawl, index, search, and job logic
docker/atlas/         Backend container image
docker-compose.yml    Local API, worker, and NATS stack
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
uv run atlas index https://example.com --max-depth 1
```

Run a lightweight syntax check:

```sh
make check
```

## Docker Compose

Start the API, worker, and NATS:

```sh
docker compose up --build
```

The API is published at `http://127.0.0.1:8000`.

## Configuration

Copy `.env.example` to `.env` when local secrets are needed.

`OPENROUTER_API_KEY` is only needed when Atlas must generate a DuckDuckGo extraction schema for search. Once `backend/.schemas/search.duckduckgo.json` exists, search can reuse that cached schema.
