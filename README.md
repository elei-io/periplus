# Periplus

Periplus acquires web documents, retains immutable content-addressed bytes, records observed evidence
in DuckLake, and maintains rebuildable structural and URL relations.

The currently delivered path is:

```text
crawl plan -> immutable bytes -> ingest.* -> material.* -> web.* / content.*
```

Acquisition-plan edges are page-local DuckDB queries and do not require a SQL
compiler or historical catalogue connection.
The public SQL interface is exactly four portable DuckLake views under `web.*` and `content.*`:
observations, link occurrences, immutable content objects, and HTML elements. The matching C++
extension supplies hosted query safety and measured optimizer rules without adding public
semantics. The bounded read-only SQL console exposes only qualified public relations in both the
web application and the `periplus` terminal client; physical `ingest.*` and `material.*` relations
remain Periplus implementation details.

The TypeScript workspace keeps its distributable clients under `packages/`:
`periplus-console-core`, `periplus-web-shell`, and `periplus-terminal-shell`. The deployable React
application lives under `web/` and consumes `periplus-web-shell`.
The locally installable Python SDK lives under `packages/periplus-python-sdk/`.

The backend is one `periplus` Python package organized by capability under `backend/src/periplus/`.
The API, crawler, ingestor, materializer, and janitor are independently runnable process roles
from the same package.

## Development

Requirements are Python 3.14 with `uv`, Node.js 22 or newer, Docker Compose, the sibling
`periplus-duckdb-extension` checkout, the pinned
`quack/ducklake-cdc-extension-1.5.5` checkout, and a standard CDP endpoint. The default Compose
stack builds both matching Linux extensions in cached builder stages, then provisions separate
Postgres authorities for Periplus control state and DuckLake metadata, S3-compatible lake storage, an
immutable object repository, and JetStream without external credentials.

```sh
cp .env.example .env
make sync
make check
make compose-up
```

The first image build compiles DuckDB and both extensions. Later builds reuse those layers until
the pinned DuckDB version or extension source changes. Runtime images contain only the compiled
extension artifacts, not the compiler toolchain.

If a greenfield baseline replacement leaves either local database or another disposable service
with a superseded contract, reset the complete development state and start again:

```sh
make compose-reset
make compose-up
```

This resets both Postgres authorities, lake objects and gateway metadata, the shared immutable
repository, and JetStream.

Run one query from the terminal:

```sh
npm run periplus -- 'SELECT count(*) FROM web.observation'
```

Run `npm run periplus` without SQL to open the interactive terminal. Set `PERIPLUS_API_URL` when the API
is not available at `http://127.0.0.1:8000`. Inside either shell, `.tables` lists the public
catalogue, `.describe content.object` shows an object's columns, and `.history` shows recent input.
`.help` lists all local commands. The Periplus web
home page provides catalogue assistance with Markdown answers, result tables, and validated SQL
drafts that can be copied or run directly in the conversation.

Useful commands:

```sh
make setup
make catalogue-check
make api
make crawler
make ingestor
make materializer
make janitor
```

The canonical product and data contracts are:

- [architecture](docs/ARCHITECTURE.md)
- [deployment](docs/DEPLOYMENT.md)
- [cutoff](docs/CUTOFF.md)
- [schema](docs/SCHEMA.md)
- [lifecycle](docs/LIFECYCLE.md)
- [query](docs/QUERY.md)
- [DuckDB extension development](docs/EXTENSION_DEVELOPMENT.md)
- [vision](docs/VISION.md)
