# Deployment

Periplus deployment names distinguish infrastructure authorities, executable process roles, and
domain capabilities.

## Naming

- Infrastructure uses ownership-first names: `lake-alluxio-s3`, `lake-postgres`,
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
| `lake-alluxio-master` | Local Alluxio namespace and asynchronous persistence coordination | `lake-alluxio-journal` |
| `lake-alluxio-worker` | Local SSD working set and persistence job execution | `lake-alluxio-worker-data` |
| `lake-alluxio-namespace-init` | One-shot creation of Alluxio's `raw` and `lake` S3 bucket directories | none |
| `lake-alluxio-s3` | Alluxio's S3-compatible proxy; it is not a storage authority | none |
| Backblaze B2 under-store | Durable raw objects and DuckLake files behind Alluxio | configured bucket and prefix |
| `lake-postgres` | DuckLake metadata only | `lake-postgres-data` |
| `periplus-postgres` | Periplus editable control and current execution state only | `periplus-postgres-data` |
| `periplus-nats` | JetStream/KV delivery, presence, pacing, and leases | `periplus-nats-data` |
| `periplus-crawler` | Crawl graph execution, page acquisition, and immutable raw-object writes | Alluxio `raw` namespace |
| `periplus-ingestor` | Immutable crawl and visit evidence writes | none |
| `periplus-materializer` | Fixed projections, rebuilds, and live CDC | Alluxio `raw` reads and `lake` writes |
| `periplus-api` | HTTP API | Alluxio `raw` namespace |
| `periplus-admin` | Authenticated operator application and API gateway | none |
| `periplus-public` | Public Next.js application and bounded API client | none |
| `periplus-janitor` | Periplus-owned staging, navigation, and runtime cleanup | Alluxio `raw` namespace |
| `periplus-setup` | One-shot schema and catalogue installation | none |

`PERIPLUS_CONTROL_DATABASE_URL` always identifies Periplus Postgres.
`PERIPLUS_DUCKLAKE_METADATA_PATH` independently identifies the DuckLake metadata store. They must
never target the same database in the maintained Compose deployment.
For platform integration, `PERIPLUS_DUCKLAKE_METADATA_PATH` accepts either DuckLake's native
`postgres:...` attach form or a standard `postgresql://...`/`postgres://...` URL. Periplus normalizes
standard URLs to the native DuckLake form at its configuration boundary.

Deleting `periplus-postgres-data` loses editable plans, schedules, policies, and current execution
state without deleting lake history. Deleting `lake-postgres-data` loses the DuckLake catalogue;
the B2 objects alone are not a usable lake. A local Compose reset removes both databases, NATS,
the Alluxio journal and cache. It intentionally does not delete the remote B2 prefix, so raw objects
remain and files from a reset catalogue become unreferenced remote objects until they are reclaimed
explicitly.

The default Compose deployment keeps all local persistent state in named volumes, so
`docker compose down --volumes` is a complete local reset; it leaves the B2 under-store untouched.
A direct-filesystem DuckLake deployment is a
separate production configuration: set `PERIPLUS_DUCKLAKE_DATA_PATH` to an absolute shared path and
mount that path consistently into every Periplus process that writes or reads lake files.

## Environment

The checked-in `.env.example` is the canonical runnable development contract. It separates:

- Compose build inputs and published ports;
- Periplus control Postgres from DuckLake metadata Postgres;
- Alluxio's local cache capacity and S3 identity from Periplus connection settings;
- host addresses from container-network addresses; and
- required attachment settings from optional storage and operational overrides.

Application code does not infer a DuckLake metadata store or data path.
`PERIPLUS_DUCKLAKE_METADATA_PATH` and `PERIPLUS_DUCKLAKE_DATA_PATH` are required. Standard provider
credentials such as `AWS_*` remain valid where the selected protocol supports them.

The development S3 endpoint is the Alluxio OSS proxy at `/api/v1/s3`. It uses `ASYNC_THROUGH`
from one SSD-class worker tier and the S3 streaming uploader into the configured Backblaze B2 bucket
and prefix. The official OSS
image is amd64-only, so Docker Desktop uses emulation on Apple Silicon. Alluxio SIMPLE authentication
interprets the client-facing S3 key ID as an Alluxio/Unix identity and does not validate that secret;
the local default therefore uses the image's `alluxio` user. B2 uses a separate bucket-scoped S3
application key supplied only through the ignored `.env` file. These remain single-node local cache
defaults, not the production replication, authentication, or topology contract.

Alluxio exposes two top-level S3 namespaces over the same B2 under-store. `raw` contains immutable
source documents at `raw/html/...` and `raw/documents/...`; repository-relative keys omit that bucket
name and begin with `html/...` or `documents/...`. `lake` is exclusively the DuckLake data root, so
DuckLake and Periplus materialization files appear under `lake/...`. Raw-object and lake-file
ownership remain separate even though both use the same local cache and remote bucket.

The preferred development configuration uses a dedicated B2 bucket with an empty
`LAKE_B2_PREFIX`. A non-empty prefix must already exist as an object-store directory before Alluxio
starts; nested prefixes require each ancestor directory marker.

The Compose build still compiles the Periplus and DuckLake CDC extensions together against the pinned
DuckDB version. `PERIPLUS_DUCKDB_EXTENSION_REPO` and `PERIPLUS_DUCKLAKE_CDC_EXTENSION_REPO` only relocate
the two additional build contexts; they do not change the extension build.

## Production artifacts

GitHub Actions publishes four immutable artifacts for each main-branch revision:

- `ghcr.io/ekkuleivonen/periplus-core:sha-<commit>` contains the API, every worker role,
  `periplus-setup`, the Periplus DuckDB extension, and the DuckLake CDC extension;
- `ghcr.io/ekkuleivonen/periplus-admin:sha-<commit>` contains the operator UI and authenticated nginx gateway;
- `ghcr.io/ekkuleivonen/periplus-public:sha-<commit>` contains the standalone Next.js public application; and
- `oci://ghcr.io/ekkuleivonen/periplus-charts/periplus:0.1.0-dev.<commit>` deploys the three images.

Release tags `vX.Y.Z` additionally publish matching `X.Y.Z` image and chart versions. Production
GitOps must pin the explicit chart version and all three explicit image tags; it must not consume a
mutable `latest` tag.

The production extension sources and DuckDB version are pinned in
`.github/extension-sources.env`. The Periplus extension repository is private, so the Periplus GitHub
repository uses an `PERIPLUS_EXTENSION_DEPLOY_KEY` Actions secret whose public half is a read-only
deploy key on that repository; DuckLake CDC is public. BuildKit receives clean checkouts of both
exact revisions as named build contexts. The backend Dockerfile compiles both native artifacts
against the pinned DuckDB source and embeds them under `/opt/periplus`; extensions are never
downloaded or mounted at runtime.

## Kubernetes topology

The chart under `charts/periplus` owns only Periplus processes. PostgreSQL, NATS JetStream, S3-compatible
storage, the standard CDP endpoint, secret projection, ingress, and metrics storage remain external
platform authorities. The chart supports one API, one janitor, scalable crawler/ingestor/
materializer deployments, and independently scalable stateless admin and public replicas.

`periplus-setup` is a blocking Helm pre-install and pre-upgrade hook. A failed migration or catalogue
bootstrap prevents the new runtime image from rolling out. All backend workloads in one release
must use the same immutable image tag, keeping Python code, the DuckDB runtime, catalogue schema,
and both native extensions on one release identity.

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

`PERIPLUS_PUBLIC_RECEIPT_SECRET` is a separate random secret of at least 32 characters,
shared by all public replicas. Rotating it invalidates outstanding request receipts.
The Helm chart references `secrets.apiAccess` for these three values and does not create
credentials. The public application caps anonymous submissions at ten per minute per
process. The platform ingress owns aggregate limits across replicas and the CDP service's
network policy must prevent access to private/internal destinations, including redirects.

Compose publishes public on localhost:8080, admin on localhost:8081, and core on localhost:8000.
The three images are independently buildable under `docker/periplus`, `docker/admin`, and
`docker/public`. Application health probes use `/healthz` for core/admin and `/api/healthz`
for public, without forwarding health probes into administrative APIs.
