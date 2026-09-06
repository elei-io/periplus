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

## Products and packages

| Package | Owns |
| --- | --- |
| `packages/periplus/` | Crawl execution, ingestion, materialization, catalogue and infrastructure APIs. |
| `packages/periplus-admin/` | Authenticated operator UI for plans, policies, schedules, runs, workers and catalogue maintenance. |
| `packages/periplus-public/` | Single-page SQL interface over the isolated Python query server. |

The core API, crawler, ingestor, materializer, setup and janitor are process roles of
one Python package. Admin is a Vite application served by an authenticated nginx gateway;
public is a Next.js application whose server holds the restricted infrastructure credential.
Neither frontend imports core implementation code or connects directly to its stores.

The console core, browser shell, terminal shell and Python SDK remain supporting packages.
`_web_old_dont_touch/` is an untouched historical reference, excluded from workspaces and images.

## Development

Requirements are Python 3.14 with `uv`, Node.js 24 or newer, Docker Compose, the pinned
`ducklake-cdc-extension` checkout, and a standard CDP endpoint. The default Compose
stack builds the matching DuckLake CDC extension in cached builder stages, then provisions separate
Postgres authorities for Periplus control state and DuckLake metadata, and a VersityGW S3 service
with `raw/*` source objects and `lake/*` DuckLake files stored in a local named volume.
JetStream owns work delivery. Storage runs natively on Apple Silicon and needs no cloud account.
Copy `.env.example` and supply three distinct API service secrets before starting Compose.
Generate each secret with
`openssl rand -hex 32`; the required names are in `.env.example`.

```sh
cp .env.example .env
make sync
make check
make compose-up
```

The first image build compiles the DuckLake CDC extension against DuckDB. Later builds reuse those layers until
the pinned DuckDB version or CDC extension source changes. Runtime images contain only the compiled
extension artifacts, not the compiler toolchain.

If a greenfield baseline replacement leaves either local database or another disposable service
with a superseded contract, reset the complete development state and start again:

```sh
make compose-reset
make compose-up
```

This resets both Postgres authorities, all local raw and lake objects, and JetStream.

Run one query from the terminal:

```sh
npm run periplus -- 'SELECT count(*) FROM web.observation'
```

Run `npm run periplus` without SQL to open the interactive terminal. Set `PERIPLUS_QUERY_URL` and `PERIPLUS_QUERY_API_TOKEN` when the query server
is not available at `http://127.0.0.1:8000`. Inside either shell, `.tables` lists the public
catalogue, `.describe content.object` shows an object's columns, and `.history` shows recent input.
`.help` lists all local commands. Set `PERIPLUS_API_TOKEN` to the public service token for
terminal SQL, or to the admin token for SDK operational calls.

Start the applications locally with `npm run dev --workspace periplus-public` and
`npm run dev --workspace periplus-admin`. Both read root `.env` during development.
Compose exposes public on port 8080 and admin on port 8081. Admin uses HTTP Basic
login with username `admin` and the administrative API token as password.
Use TLS at the ingress in production.

Public crawl submission acquires one URL, follows no links, and has a five-minute
run deadline. Save the returned page URL to track the request for seven days.
Receipts are bearer capabilities: anyone holding one can read that request's progress.
There is no account system or public request database. Submission is capped at ten
requests per minute per public process; production ingress owns aggregate rate limits.


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
