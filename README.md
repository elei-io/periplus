# Atlas

Atlas turns web pages into durable, queryable evidence. It acquires a page once, keeps the
content-addressed raw HTML, and stores a structural projection for later search, extraction, and
analysis.

The project is intentionally small: one page-acquisition path, one repository boundary, and a
clear owner for every kind of state. Atlas scales worker capabilities independently while one
narrow, KV-backed Resource Governor contract bounds shared pressure on remote sites, DuckLake, and
S3 / MinIO.

Atlas is organized around crawl graphs:

- a graph groups user metadata, nodes, and edges;
- a node accepts URL inputs and maps admitted inputs to the single `crawl` acquisition primitive;
- an edge runs bounded, crawl-scoped SQL over the current page package, optionally joins the
  graph run's pinned DuckLake snapshot, and passes returned URLs to another node;
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

Compose starts the complete local stack, including a development Quack server, exposes the web UI
at `http://127.0.0.1:8080`, and exposes the API at `http://127.0.0.1:8000`. The accepted
target topology separates CDP acquisition, ingestion, materialization, and maintenance failure
domains. The configured CDP service owns acquisition transport and browser-farm capacity.
[AUDIT.md](AUDIT.md) tracks the direct greenfield cutover.

Compose does not bind-mount the application source tree. After changing Atlas code, rebuild and
recreate the services with `docker compose up --build -d` (or `make compose-up`) so every running
container uses the code installed in its image.

Run the API without Compose:

```sh
make api
```

`ATLAS_QUACK_URI` and `ATLAS_QUACK_TOKEN` identify the private Quack endpoint reachable only by
Atlas API compute. The API owns a bounded pool of reusable Quack client connections for validated,
read-only workbench queries and Arrow IPC result streaming. The browser never receives Quack,
Postgres, DuckLake, or object-store credentials.

The home page runs a server-owned PydanticAI catalogue agent when `OPENAI_API_KEY` is configured.
It streams progress, bounded query rows, executed SQL, and a short evidence-based summary over SSE.
`ATLAS_SEARCH_MODEL` selects the PydanticAI model identifier; catalogue tools retain the same
read-only validator, permits, timeouts, and result limits as the SQL workbench.

Use the CLI for configuration and repository administration:

```sh
cd backend
uv run atlas --help
```

Create and trigger crawl graphs through the `/crawl-graphs` API or the Crawl Graphs web interface.

Useful development commands:

```sh
make check                  # compile backend modules and run unit tests
make acquisition-worker     # run `atlas-worker acquisition`
make ingestion-worker       # run `atlas-worker ingestion`
make materialization-worker # run `atlas-worker materialization`
make maintenance-worker     # run `atlas-worker maintenance`
make setup                  # create databases, migrate, and bootstrap the catalogue
make catalogue-check        # validate the DuckLake catalogue
make catalogue-benchmark    # benchmark service reads and partition/DOM SQL paths
make compose-down           # stop the local stack
```

Configuration is documented alongside its defaults in [`.env.example`](.env.example).

## Documentation

- [Vision](docs/VISION.md) — what Atlas is for, and what it is not.
- [Architecture](docs/ARCHITECTURE.md) — components, state ownership, and execution paths.
- [Worker and resource architecture](docs/WORKER_ARCHITECTURE.md) — process boundaries, queue
  routing, resource admission, and scaling.
- [Crawl graphs](docs/CRAWL_GRAPHS.md) — graph entities, runtime semantics, messaging, and readiness.
- [Catalogue SQL](docs/CATALOGUE_SQL.md) — analytical tables and DOM-style query helpers.
- [Hazards](docs/HAZARDS.md) — mistakes and complexity traps to avoid.
- [Agent guide](AGENTS.md) — concise working rules for coding agents.
