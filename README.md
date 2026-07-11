# Atlas

Atlas turns web pages into durable, queryable evidence. It acquires a page once, keeps the
content-addressed raw HTML, and stores a structural projection for later search, extraction, and
analysis.

The project is intentionally small: one page-acquisition path, one repository boundary, and a
clear owner for every kind of state. Atlas favors bounded local work and explicit operational
limits over speculative distributed machinery.

Its primary actions are:

- `crawl` — acquire and retain pages.
- `index` — perform a bounded breadth-first walk.
- `search` — find relevant pages.
- `extract` — produce structured data or query-parameter schemas from acquired pages.

## Quick start

Requirements:

- Python 3.14 and [uv](https://docs.astral.sh/uv/) for backend development.
- Docker with Docker Compose for the complete local stack.

```sh
cp .env.example .env
make sync
make check
make compose-up
```

Compose starts Postgres, NATS, the API, task worker, and repository ingestor. The API is available
at `http://127.0.0.1:8000`.

Run the API without Compose:

```sh
make api
```

Use the CLI against a running API:

```sh
cd backend
uv run atlas --help
uv run atlas crawl https://example.com
uv run atlas index https://example.com --max-depth 1
uv run atlas schema https://example.com --prompt "Extract article cards."
uv run atlas extract https://example.com --prompt "Extract the main heading."
```

Useful development commands:

```sh
make check                  # compile backend modules and run unit tests
make db-upgrade             # apply Postgres migrations
make catalogue-check        # validate the DuckLake catalogue
make compose-down           # stop the local stack
```

Configuration is documented alongside its defaults in [`.env.example`](.env.example).

## Documentation

- [Vision](docs/VISION.md) — what Atlas is for, and what it is not.
- [Architecture](docs/ARCHITECTURE.md) — components, state ownership, and execution paths.
- [Hazards](docs/HAZARDS.md) — mistakes and complexity traps to avoid.
- [Agent guide](AGENTS.md) — concise working rules for coding agents.
