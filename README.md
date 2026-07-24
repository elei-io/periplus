# Atlas

Atlas turns web pages into durable, queryable evidence. It acquires a page once, keeps the
content-addressed raw HTML, and stores a structural projection for later search, extraction, and
analysis.

The project is intentionally small: one page-acquisition path, one repository boundary, and a
clear owner for every kind of state. Atlas uses bounded local client pools for managed DuckLake and
object-store work, while cross-replica website politeness is coordinated by independent per-domain
NATS keys.

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
- Docker with Docker Compose for Atlas application processes.
- Remote Postgres, an Atlas NATS account, a Basin CDC NATS account, DuckBasin, an
  S3-compatible raw-object repository, and a standard CDP endpoint.

```sh
cp .env.example .env
make sync
make check
make compose-up
```

Compose starts only Atlas processes, exposes the web UI at `http://127.0.0.1:8080`, and exposes
the API at `http://127.0.0.1:8000`. Postgres, both NATS accounts, DuckLake storage and compute,
object storage, and CDP remain remote. Separate acquisition, ingestion, catalogue-ingress,
materialization, and housekeeping deployments retain independent failure and scaling boundaries.
The configured CDP service owns acquisition transport and browser-farm capacity.
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/WORKER_ARCHITECTURE.md](docs/WORKER_ARCHITECTURE.md) define the managed-lake boundary.

Compose does not bind-mount the application source tree. After changing Atlas code, rebuild and
recreate the services with `docker compose up --build -d` (or `make compose-up`) so every running
container uses the code installed in its image.

Run the API without Compose:

```sh
make api
```

Atlas authenticates with the OAuth client credentials issued for its DuckBasin service account,
discovers `DUCKBASIN_LAKE`, refreshes its short-lived token, and mints a unique session-affine Quack
attachment for every DuckDB client. The API owns a bounded pool of those managed clients for
validated, read-only workbench queries and Arrow IPC result streaming. The browser never receives
Quack, Postgres, DuckLake, or object-store credentials.

The home page runs a server-owned PydanticAI analytics agent when `OPENAI_API_KEY` is configured.
Each question is isolated. `ATLAS_IDEA_MODEL` inspects catalogue metadata and plans three to five
schema-grounded analytical directions without running SQL. Concurrent `ATLAS_SQL_MODEL` agents
investigate them with read-only catalogue
discovery and SQL, and the idea model synthesizes their findings. Atlas stores no question, answer,
conversation, or result history. The response keeps all three directions distinct and streams every
successful analytical SQL query with its bounded rows. Catalogue tools retain the same read-only
validator, client-pool limits, timeouts, cancellation, and result limits as the SQL workbench.
The planner's finite budget is `ATLAS_IDEA_TOOL_CALL_LIMIT`; its model-request allowance is derived
from that budget so every permitted metadata call still leaves room to return the typed plan.

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
make catalogue-relay-worker # run `atlas-worker catalogue-relay`
make materialization-worker # run `atlas-worker materialization`
make housekeeping-worker    # run `atlas-worker housekeeping`
make setup                  # migrate remote Postgres and bootstrap the managed lake
make catalogue-check        # validate the DuckLake catalogue
make catalogue-benchmark    # benchmark service reads and partition/DOM SQL paths
make verify-remote-runtime  # prove Atlas KV and fresh Basin CDC delivery
make compose-down           # stop the local Atlas deployment
```

Configuration is documented alongside its defaults in [`.env.example`](.env.example).

## Documentation

- [Vision](docs/VISION.md) — what Atlas is for, and what it is not.
- [Architecture](docs/ARCHITECTURE.md) — components, state ownership, and execution paths.
- [Worker architecture](docs/WORKER_ARCHITECTURE.md) — process boundaries, queue routing,
  managed-client concurrency, and scaling.
- [Crawl graphs](docs/CRAWL_GRAPHS.md) — graph entities, runtime semantics, messaging, and readiness.
- [Catalogue SQL](docs/CATALOGUE_SQL.md) — analytical tables and DOM-style query helpers.
- [Hazards](docs/HAZARDS.md) — mistakes and complexity traps to avoid.
- [Agent guide](AGENTS.md) — concise working rules for coding agents.
