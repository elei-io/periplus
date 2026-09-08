# Periplus

Periplus acquires web documents, retains immutable content-addressed bytes, records observed evidence
in DuckLake, and maintains rebuildable structural and URL relations.

Manual and scheduled finite requests feed one shared crawler:

```text
collections + background -> shared frontier -> CDP -> immutable bytes -> ingest.* -> material.* -> web.* / content.*
```

Compatible queued requests share acquisition while retaining independent traversal and budgets.
Seed SQL selects bounded starting URLs from the corpus; follow SQL reads only the captured page's
navigation package. Postgres owns current execution, NATS owns delivery and domain permits, and
DuckLake retains observations and their durable collection/background lineage.

The public catalogue exposes observations, link occurrences, collections, fulfillments, acquisition
reasons, immutable content objects and HTML elements. The Python query service validates bounded
read-only SQL over standard DuckDB. The separately pinned DuckLake CDC extension is used only by
live materialization. Physical `ingest.*` and `material.*` remain implementation details.

## Products and packages

| Package | Owns |
| --- | --- |
| `packages/periplus/` | Crawl execution, ingestion, materialization, catalogue and infrastructure APIs. |
| `packages/periplus-admin/` | Authenticated controls for the crawler, collections, policies, workers and catalogue maintenance. |
| `packages/periplus-public/` | Public corpus discovery, SQL, Live crawler activity and collection submission. |

The core API, crawler, ingestor, materializer, setup and janitor are process roles of
one Python package. Admin is a Vite application served by an nginx API gateway;
public is a Next.js application whose server holds the restricted infrastructure credential.
Neither frontend imports core implementation code or connects directly to its stores.

The console core, browser shell, terminal shell and Python SDK remain supporting packages.
`_web_old_dont_touch/` is an untouched historical reference, excluded from workspaces and images.

## Development

Requirements are Python 3.14 with `uv`, Node.js 24 or newer, Docker Compose, and a standard
CDP endpoint. The default Compose stack installs the signed CDC community package and provisions separate
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

The core image installs CDC from the DuckDB community repository and verifies its version and
source revision against the pinned DuckDB runtime. No native extension build or sibling checkout
is required. Later application builds reuse the cached extension installation.

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
is not available at `http://127.0.0.1:8010`. Inside either shell, `.tables` lists the public
catalogue, `.describe content.object` shows an object's columns, and `.history` shows recent input.
`.help` lists all local commands. The terminal uses `PERIPLUS_QUERY_API_TOKEN`. The Python SDK uses the public application URL
(`PERIPLUS_PUBLIC_URL`) and needs no service token; see [its README](packages/periplus-python-sdk/README.md).

Start the applications locally with `npm run dev --workspace periplus-public` and
`npm run dev --workspace periplus-admin`. Both read root `.env` during development.
Compose exposes public on port 8080 and admin on port 8081. Admin has no built-in login;
production access to its UI and API gateway is enforced by Cloudflare Access.
Use TLS at the ingress in production.

Public collection submission accepts a URL or description, depth 0–2, link scope, allowed sections,
and a budget of up to 1,000 pages. All public request details are public. Save the returned request
URL to inspect current progress and durable historical arrivals. Settlement and verified query
readiness are separate milestones. Ingress owns deployment-wide request/body limits. Operators
control global/domain pacing and concurrency, priorities, exclusions and background allocation;
configured speed is an upper bound rather than guaranteed throughput.

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
