# Atlas

Atlas is a Python web crawling and search backend. It exposes the same domain logic through:

- a FastAPI HTTP API,
- a Typer CLI,
- an async NATS JetStream worker for index jobs.

Its current web primitives are `search`, `index`, `scrape`, `schema`, and `extract`.

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
uv run atlas scrape https://example.com
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

## Docker Compose

Start the API, worker, and NATS:

```sh
docker compose up --build
```

The API is published at `http://127.0.0.1:8000`.

## Configuration

Copy `.env.example` to `.env` when local secrets are needed.

`OPENROUTER_API_KEY` is only needed when Atlas must generate an extraction schema or infer a target JSON example. Generated artifacts live under `.cache/<domain>/<service>/`, so schemas are cached beside other runtime artifacts without mixing them into source folders. Page-load-sensitive commands support `--mode` and `--wait`; use `--mode app --wait stable` for pages that need frontend hydration before their data appears.

`scrape` always writes a fixed evidence bundle:

```text
.cache/<domain>/scrape/<request-id>/page.html
.cache/<domain>/scrape/<request-id>/crawl.json
.cache/<domain>/scrape/<request-id>/manifest.json
```

`crawl.json` stores reusable Crawl4AI evidence such as links, media, metadata, response headers, network logs, and console messages. `index` reads those cached links instead of rendering pages itself.
