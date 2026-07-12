# Atlas

Atlas turns web pages into durable, queryable evidence. It acquires a page once, keeps the
content-addressed raw HTML, and stores a structural projection for later search, extraction, and
analysis.

The project is intentionally small: one page-acquisition path, one repository boundary, and a
clear owner for every kind of state. Atlas favors bounded local work and explicit operational
limits over speculative distributed machinery.

Atlas is organized around crawl graphs:

- a graph groups user metadata, nodes, and edges;
- a node accepts URL inputs and maps admitted inputs to the single `crawl` acquisition primitive;
- an edge runs bounded, crawl-scoped SQL against DuckLake and passes returned URLs to another node;
  and
- self-edges express bounded recursion such as pagination or site walking.

DuckLake remains the inspection and analysis surface. Users query retained crawl evidence and build
queries, views, and materializations without requiring a separate result-rendering abstraction.

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

Compose starts Postgres, NATS, the API, runtime worker, and repository worker. The API is available
at `http://127.0.0.1:8000`.

Run the API without Compose:

```sh
make api
```

The current CLI remains available while the graph API and CLI replace action-specific commands:

```sh
cd backend
uv run atlas --help
uv run atlas crawl https://example.com
```

Useful development commands:

```sh
make check                  # compile backend modules and run unit tests
make setup                  # create databases, migrate, and bootstrap the catalogue
make catalogue-check        # validate the DuckLake catalogue
make compose-down           # stop the local stack
```

Configuration is documented alongside its defaults in [`.env.example`](.env.example).

## Documentation

- [Vision](docs/VISION.md) — what Atlas is for, and what it is not.
- [Architecture](docs/ARCHITECTURE.md) — components, state ownership, and execution paths.
- [Crawl graphs](docs/CRAWL_GRAPHS.md) — graph entities, runtime semantics, messaging, and readiness.
- [Catalogue SQL](docs/CATALOGUE_SQL.md) — analytical tables and DOM-style query helpers.
- [Hazards](docs/HAZARDS.md) — mistakes and complexity traps to avoid.
- [Agent guide](AGENTS.md) — concise working rules for coding agents.
