# Deployment

Periplus deployment names distinguish infrastructure authorities, executable process roles, and
domain capabilities.

## Naming

- Infrastructure uses ownership-first names: `lake-s3`, `lake-postgres`,
  `periplus-postgres`, and `periplus-nats`.
- Executable roles use actor names: `periplus-crawler`, `periplus-ingestor`, `periplus-materializer`, and
  `periplus-janitor`.
- Domain names remain `crawl`, `acquisition`, `ingestion`, and `materialization` in schemas,
  queues, APIs, metrics, and capability packages.

The crawler, ingestor, and materializer are horizontally scalable and must not declare a Compose
`container_name`. Singleton infrastructure, API, admin, public, janitor, and setup services have explicit
container names. This means one local Periplus Compose project may run on a Docker host at a time.

## Local topology

| Service | Authority or role | Persistent state |
| --- | --- | --- |
| `lake-s3-init` | One-shot creation of local `raw` and `lake` bucket directories | none |
| `lake-s3` | VersityGW S3 endpoint over the local filesystem | `lake-s3-data` |
| `lake-postgres` | DuckLake metadata only | `lake-postgres-data` |
| `periplus-postgres` | Periplus editable control and current execution state only | `periplus-postgres-data` |
| `periplus-nats` | JetStream/KV delivery, presence, pacing, and leases | `periplus-nats-data` |
| `periplus-crawler` | Crawl graph execution, page acquisition, and immutable raw-object writes | S3 `raw` namespace |
| `periplus-ingestor` | Immutable crawl and visit evidence writes | none |
| `periplus-materializer` | Fixed projections, rebuilds, and live CDC | S3 `raw` reads and `lake` writes |
| `periplus-query` | Isolated read-only SQL preparation and execution | none |
| `periplus-api` | Control HTTP API | S3 `raw` namespace |
| `periplus-admin` | Authenticated operator application and API gateway | none |
| `periplus-public` | Public Next.js application and bounded API client | none |
| `periplus-janitor` | Periplus-owned staging, navigation, and runtime cleanup | S3 `raw` namespace |
| `periplus-setup` | One-shot schema and catalogue installation | none |

`PERIPLUS_CONTROL_DATABASE_URL` always identifies Periplus Postgres.
`PERIPLUS_DUCKLAKE_METADATA_PATH` independently identifies the DuckLake metadata store. They must
never target the same database in the maintained Compose deployment.
For platform integration, `PERIPLUS_DUCKLAKE_METADATA_PATH` accepts either DuckLake's native
`postgres:...` attach form or a standard `postgresql://...`/`postgres://...` URL. Periplus normalizes
standard URLs to the native DuckLake form at its configuration boundary.

Deleting `periplus-postgres-data` loses editable plans, schedules, policies, and current execution
state without deleting lake history. Deleting `lake-postgres-data` loses the DuckLake catalogue;
the objects alone are not a usable lake. Deleting `lake-s3-data` loses local raw objects and lake
files. Keep the catalogue and its objects together when backing up or restoring development state.

The default Compose deployment keeps all persistent state in named volumes, so
`docker compose down --volumes` resets both databases, local objects, and NATS. No remote object
store or cloud credentials are required for development.
A direct-filesystem DuckLake deployment is a
separate production configuration: set `PERIPLUS_DUCKLAKE_DATA_PATH` to an absolute shared path and
mount that path consistently into every Periplus process that writes or reads lake files.

## Environment

The checked-in `.env.example` is the canonical runnable development contract. It separates:

- Compose build inputs and published ports;
- Periplus control Postgres from DuckLake metadata Postgres;
- local S3 service credentials from host-side Periplus connection settings;
- host addresses from container-network addresses; and
- required attachment settings from optional storage and operational overrides.

Application code does not infer a DuckLake metadata store or data path.
`PERIPLUS_DUCKLAKE_METADATA_PATH` and `PERIPLUS_DUCKLAKE_DATA_PATH` are required. Standard provider
credentials such as `AWS_*` remain valid where the selected protocol supports them.

The development S3 endpoint is VersityGW's POSIX backend at `http://lake-s3:7070` inside
Compose and `http://127.0.0.1:7070` on the host (port configurable with `LAKE_S3_PORT`). The pinned
multi-architecture image runs natively on Apple Silicon. One named volume contains two buckets:
`raw` owns immutable source documents with repository-relative keys such as `html/...` and
`documents/...`; `lake` is exclusively the DuckLake data root. The bucket initializer is idempotent.
Versity validates S3 signatures using `LAKE_S3_KEY_ID` and `LAKE_S3_SECRET_ACCESS_KEY`; Compose
supplies the same credentials to its writers. Host-side client credentials in `.env` must match.
These local root credentials must never be given to public clients.

The S3 provider, caching, replication, and public-access infrastructure are production deployment
choices. Periplus uses its existing generic S3 connection settings; the production chart does not
provision Versity. Local development no longer depends on a remote under-store.

The Compose build compiles only the DuckLake CDC extension against the pinned DuckDB version.
`PERIPLUS_DUCKLAKE_CDC_EXTENSION_REPO` locates its source checkout. Query connections use standard
DuckDB and official storage extensions, with no custom query extension.

## Production artifacts

GitHub Actions publishes four immutable artifacts for each main-branch revision:

- `ghcr.io/ekkuleivonen/periplus-core:sha-<commit>` contains the API, every worker role,
  `periplus-setup`, the DuckLake CDC extension;
- `ghcr.io/ekkuleivonen/periplus-admin:sha-<commit>` contains the operator UI and authenticated nginx gateway;
- `ghcr.io/ekkuleivonen/periplus-public:sha-<commit>` contains the standalone Next.js public application; and
- `oci://ghcr.io/ekkuleivonen/periplus-charts/periplus:0.1.0-dev.<commit>` deploys the three images.

Release tags `vX.Y.Z` additionally publish matching `X.Y.Z` image and chart versions. Production
GitOps must pin the explicit chart version and all three explicit image tags; it must not consume a
mutable `latest` tag.

The production extension sources and DuckDB version are pinned in
`.github/extension-sources.env`. BuildKit receives the pinned public DuckLake CDC source as a named build context. The backend
Dockerfile builds its native artifact against pinned DuckDB and embeds it under `/opt/periplus`.
No private extension checkout or deploy key is required. Official DuckDB storage extensions are
installed into the image at build time.

## Kubernetes topology

The chart under `charts/periplus` owns only Periplus processes. PostgreSQL, NATS JetStream, S3-compatible
storage, the standard CDP endpoint, secret projection, ingress, and metrics storage remain external
platform authorities. The chart supports one API, one janitor, scalable crawler/ingestor/
materializer deployments, and independently scalable stateless admin and public replicas.

`periplus-setup` is a blocking Helm pre-install and pre-upgrade hook. A failed migration or catalogue
bootstrap prevents the new runtime image from rolling out. All backend workloads in one release
must use the same immutable image tag, keeping Python code, the DuckDB runtime, catalogue schema,
and the CDC extension on one release identity.

Control state and DuckLake metadata still require distinct PostgreSQL databases in Kubernetes.
The same S3 bucket may back raw repository objects and DuckLake data when the repository prefix and
DuckLake data path do not overlap. Annotated API and worker Services expose all built-in Prometheus
endpoints for platform discovery.

## Process entrypoints

The shared Periplus runtime image exposes:

```sh
periplus-worker crawler
periplus-worker ingestor
periplus-worker materializer
periplus-worker janitor
```

Process-specific configuration follows the same actor names:
`PERIPLUS_CRAWLER_*`, `PERIPLUS_INGESTOR_*`, `PERIPLUS_MATERIALIZER_*`, and
`PERIPLUS_JANITOR_*`. Capability contracts such as ingestion subjects and materialization metrics
retain their domain terminology.

The maintained development replica baseline is one crawler, four ingestors, and four
materializers. `PERIPLUS_INGESTOR_CONCURRENCY` and `PERIPLUS_MATERIALIZER_CONCURRENCY` remain
per-process lane bounds; replica count and local concurrency are separate controls.

## Application access

Core requires distinct `PERIPLUS_ADMIN_API_TOKEN` and `PERIPLUS_PUBLIC_API_TOKEN` values.
Missing or equal credentials fail closed. Only health and Prometheus metrics are anonymous;
keep the API on the private service network. Admin injects the administrative credential
server-side and protects all UI and proxied API requests with HTTP Basic authentication
(username `admin`, password the administrative token). Expose admin only through a separate
TLS ingress with operator access controls. Public receives only its restricted token.

The public app receives `PERIPLUS_QUERY_URL` and `PERIPLUS_QUERY_API_TOKEN` for SQL, plus
`PERIPLUS_API_URL` and the restricted `PERIPLUS_PUBLIC_API_TOKEN` for coverage-request submission
and public status listing. Next.js only transports these requests; Python validates and stores
them in Periplus Postgres. Public credentials cannot start crawls. Admin proxies
`/api/query/*` to the same query server with the query token. Core uses its separate API tokens.
The query service uses the core image but its own `entrypoints/query.py` composition root.
It has one connection and admission slot per process; replica count scales query capacity.

Compose's `query-access-init` runs after catalogue setup and provisions a metadata reader role
plus a Versity user with only GetObject access to `lake/*`. Future tables created by the lake
owner inherit SELECT grants. Writer credentials are present only in this one-shot provisioning
process, never in the query server. Versity user definitions persist in `lake-s3-iam`.

In production, provision the distinct `secrets.queryDucklakeMetadata` and `secrets.queryS3`
identities with the same privileges, including default SELECT privileges for every metadata
writer role. The query deployment intentionally does not inherit shared `extraEnvFrom` or
`extraEnv`, which could inject writer secrets. Its pod must have no write-capable storage
identity from the surrounding platform. Ingress owns request/body limits and aggregate traffic
protection. Query memory, spill, runtime, and response limits are documented in QUERY.md.

Compose publishes the query server at localhost:8010, configurable with `PERIPLUS_QUERY_PORT`.
Compose publishes public on localhost:8080, admin on localhost:8081, and core on localhost:8000.
The three images are independently buildable under `docker/periplus`, `docker/admin`, and
`docker/public`. Application health probes use `/healthz` for core/admin and `/api/healthz`
for public, without forwarding health probes into administrative APIs.

The public Next.js process optionally receives `OPENAI_API_KEY` and `PERIPLUS_AI_MODEL` for its
web discovery assistant. Helm configures these through `public.ai.model` and
`public.ai.existingSecret` / `public.ai.apiKeyKey`. These never reach browser bundles. Python source discovery has its own model setting.
The assistant streams with Vercel AI SDK, limits runs to seven model steps and 90 seconds,
and admits two active runs per process. Ingress must provide deployment-wide rate and body limits.

## Coverage source discovery

The API process automatically dispatches coverage requests through the existing crawl runtime.
URL submissions require no model credentials. Text requests use `OPENAI_API_KEY`,
`BRAVE_SEARCH_API_KEY`, and `PERIPLUS_COVERAGE_MODEL` (a model supporting Responses structured
outputs). Compose supplies these only to the control API; query and crawler processes do not
receive them. Configure Helm `api.coverage.model` and `api.coverage.existingSecret`, containing
keys named by `openaiKeyKey` and `braveKeyKey`. Credentials remain server-side.

Provider configuration failures leave requests pending and retry after a minute. Search queries,
completed search steps, selected URLs, and dispatch identity are durable in operational Postgres.
No approval step is required. Website pacing and worker limits remain owned by the existing
crawler/CDP infrastructure. The CDP service must have private-network egress restrictions;
validating initial URL DNS in Python alone does not constrain redirects or subresources.
