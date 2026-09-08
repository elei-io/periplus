# Periplus

Query observed websites with SQL. Periplus captures web pages, preserves their
source evidence, and exposes a shared corpus of HTML structure and links.

For example, inspect a small sample of captured pages:

```sql
SELECT capture_id, requested_url, effective_url
FROM public_v1.capture
LIMIT 10;
```

Periplus is in research preview. Public crawl submissions, their details, and
collected evidence are shared; do not submit private information. Collection
completion and query readiness are separate milestones. Coverage, freshness,
and retention depend on the deployment's policies. SQL and parameters are
recorded privately for up to 30 days; query results are not stored in that history.

To query an existing deployment, open its public web application or use the
[Python SDK](packages/periplus-python-sdk/README.md). Self-hosting creates your
own corpus; it does not copy another deployment's data. The current SQL contract
is [public_v1](docs/SCHEMA.md); historical audit documents may describe older
contracts.

## How it works

Periplus acquires web documents, retains immutable content-addressed bytes, records observed evidence
in DuckLake, and maintains rebuildable structural and URL relations.

Manual and scheduled finite requests feed one shared crawler:

```text
manual + scheduled collections -> shared frontier -> CDP -> immutable bytes -> ingest.* -> material.* -> public_v1.*
```

Compatible queued requests share acquisition while retaining independent traversal and budgets.
Seed SQL selects bounded starting URLs from the corpus; follow SQL reads only the captured page's
navigation package. Postgres owns current execution, NATS owns delivery and domain permits, and
DuckLake retains observations and their durable collection lineage.

The public SQL catalogue exposes captures, immutable content, HTML structure,
and observed links. Collection progress and lineage are available through the
collection APIs. The Python query service validates bounded
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

Before starting Compose, configure `PERIPLUS_CDP_URL` for host-side crawling and
`PERIPLUS_COMPOSE_CDP_URL` for the endpoint reachable from containers. A browser
service is not included. The default container address assumes Docker Desktop;
on other hosts, supply an address reachable from the Compose network. Keep CDP
private and enforce browser egress restrictions as described in [SECURITY.md](SECURITY.md).

```sh
cp .env.example .env
make sync
make check
make compose-up
```

The core image installs CDC from the DuckDB community repository and verifies its version and
source revision against the pinned DuckDB runtime. No native extension build or sibling checkout
is required. Later application builds reuse the cached extension installation.

Open [the local public app](http://localhost:8080) and submit one page with depth
0 and the smallest available budget. Wait for query readiness, then run the example SQL above.
Assistant and text-discovery features require their optional provider settings;
URL submissions and direct SQL do not. An empty corpus returns no capture rows.
The default stack runs multiple writer replicas, two databases, NATS, and S3;
browser capacity and storage add to its resource use. No validated minimum RAM
or sustained-throughput guarantee is published yet.

If a greenfield baseline replacement leaves either local database or another disposable service
with a superseded contract, reset the complete development state and start again:

```sh
make compose-reset
make compose-up
```

This resets both Postgres authorities, all local raw and lake objects, and JetStream.

Run one query from the terminal:

```sh
npm run periplus -- 'SELECT capture_id FROM public_v1.capture LIMIT 10'
```

Run `npm run periplus` without SQL to open the interactive terminal. Set `PERIPLUS_QUERY_URL` and `PERIPLUS_QUERY_API_TOKEN` when the query server
is not available at `http://127.0.0.1:8010`. Inside either shell, `.tables` lists the public
catalogue, `.describe public_v1.content` shows an object's columns, and `.history` shows recent input.
`.help` lists all local commands. The terminal uses `PERIPLUS_QUERY_API_TOKEN`. The Python SDK uses the public application URL
(`PERIPLUS_PUBLIC_URL`) and needs no service token; see [its README](packages/periplus-python-sdk/README.md).

Start the applications locally with `npm run dev --workspace periplus-public` and
`npm run dev --workspace periplus-admin`. Both read root `.env` during development.
Compose exposes public on port 8080 and admin on port 8081. Admin has no built-in login;
production access to its UI and API gateway is enforced by Cloudflare Access.
Use TLS at the ingress in production.

Public collection submission accepts a URL or description, depth, link scope, allowed sections,
and a page budget. Available values come from the deployment's public access policy.
For the smoke test, choose depth 0 and the smallest available budget; one starting
URL with depth 0 acquires at most one page. All public request details are public. Save the returned request
URL to inspect current progress and durable historical arrivals. Settlement and verified query
readiness are separate milestones. Ingress owns deployment-wide request/body limits. Operators
control global/domain pacing and concurrency, priorities, exclusions and scheduled requests;
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

## License and contributions

Copyright (c) 2026 Ekku Leivonen (elei.io). The platform is AGPL-3.0-only;
the standalone Python SDK is Apache-2.0. See [LICENSING.md](LICENSING.md) for
package boundaries and third-party content rights, [CONTRIBUTING.md](CONTRIBUTING.md)
for development expectations, and [SECURITY.md](SECURITY.md) for private reports.
