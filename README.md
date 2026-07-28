# Atlas

Atlas acquires web documents, retains immutable content-addressed bytes, records observed evidence
in DuckLake, and maintains rebuildable structural and URL relations through CDC.

The currently delivered path is:

```text
crawl plan -> immutable bytes -> ingest.* -> CDC -> material.*
```

Acquisition-plan edges are page-local DuckDB queries and do not require a SQL
compiler or historical catalogue connection.
Compilation, the `web.*` semantic interface, user views/macros, and user-authored materializations
are intentionally unavailable in the current milestone. The replacement begins with portable,
versioned DuckLake views and macros, followed by a Python SDK for connection and control
ergonomics; one optional C++ extension may later optimize measured plan gaps. A bounded read-only
SQL console remains available for direct inspection of `ingest.*` and `material.*` in both the web
application and the `atlas` terminal client.

The TypeScript workspace keeps its distributable clients under `packages/`:
`atlas-console-core`, `atlas-web-shell`, and `atlas-terminal-shell`. The deployable React
application lives under `web/` and consumes `atlas-web-shell`.

The backend is one `atlas` Python package organized by capability under `backend/src/atlas/`.
API, acquisition, ingestion, CDC, materialization, and housekeeping remain independently runnable
process roles from the same package.

## Development

Requirements are Python 3.14 with `uv`, Node.js 22 or newer, Docker Compose, DuckBasin credentials,
Basin CDC NATS credentials, an S3-compatible raw-object repository, and a standard CDP endpoint.

```sh
cp .env.example .env
make sync
make check
make compose-up
```

If a greenfield baseline replacement leaves local Postgres stamped at a
revision that no longer exists, reset only the disposable control plane and
start again:

```sh
make compose-reset-control
make compose-up
```

This preserves DuckLake, repository objects, and local NATS state.

Run one query from the terminal:

```sh
npm run atlas -- 'SELECT count(*) FROM ingest.visits'
```

Run `npm run atlas` without SQL to open the interactive terminal. Set `ATLAS_API_URL` when the API
is not available at `http://127.0.0.1:8000`.

Useful commands:

```sh
make setup
make catalogue-check
make api
make acquisition-worker
make ingestion-worker
make cdc-worker
make materialization-worker
make housekeeping-worker
```

The canonical product and data contracts are:

- [architecture](docs/ARCHITECTURE.md)
- [cutoff](docs/CUTOFF.md)
- [schema](docs/SCHEMA.md)
- [lifecycle](docs/LIFECYCLE.md)
- [query](docs/QUERY.md)
- [vision](docs/VISION.md)
