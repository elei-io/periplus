# Deployment

Periplus deployment names distinguish infrastructure authorities, executable process roles, and
domain capabilities.

## Naming

- Infrastructure uses ownership-first names: `lake-s3`, `lake-postgres`, `periplus-postgres`, and
  `periplus-nats`.
- Executable roles use actor names: `periplus-crawler`, `periplus-ingestor`, `periplus-materializer`, and
  `periplus-janitor`.
- Domain names remain `crawl`, `acquisition`, `ingestion`, and `materialization` in schemas,
  queues, APIs, metrics, and capability packages.

The crawler, ingestor, and materializer are horizontally scalable and must not declare a Compose
`container_name`. Singleton infrastructure, API, web, janitor, and setup services have explicit
container names. This means one local Periplus Compose project may run on a Docker host at a time.

## Local topology

| Service | Authority or role | Persistent state |
| --- | --- | --- |
| `lake-s3` | S3-compatible DuckLake object storage | `lake-s3-data` and `lake-s3-metadata` |
| `lake-postgres` | DuckLake metadata only | `lake-postgres-data` |
| `periplus-postgres` | Periplus editable control and current execution state only | `periplus-postgres-data` |
| `periplus-nats` | JetStream/KV delivery, presence, pacing, and leases | `periplus-nats-data` |
| `periplus-crawler` | Crawl graph execution and page acquisition | shared `periplus-repository-data` |
| `periplus-ingestor` | Immutable crawl and visit evidence writes | shared `periplus-repository-data` |
| `periplus-materializer` | Fixed projections, rebuilds, and live CDC | shared `periplus-repository-data` |
| `periplus-api` | HTTP API | shared `periplus-repository-data` |
| `periplus-web` | Web application | none |
| `periplus-janitor` | Periplus-owned staging, navigation, and runtime cleanup | shared `periplus-repository-data` |
| `periplus-setup` | One-shot schema and catalogue installation | shared `periplus-repository-data` |

`PERIPLUS_CONTROL_DATABASE_URL` always identifies Periplus Postgres.
`PERIPLUS_DUCKLAKE_METADATA_PATH` independently identifies the DuckLake metadata store. They must
never target the same database in the maintained Compose deployment.
For platform integration, `PERIPLUS_DUCKLAKE_METADATA_PATH` accepts either DuckLake's native
`postgres:...` attach form or a standard `postgresql://...`/`postgres://...` URL. Periplus normalizes
standard URLs to the native DuckLake form at its configuration boundary.

Deleting `periplus-postgres-data` loses editable plans, schedules, policies, and current execution
state without deleting lake history. Deleting `lake-postgres-data` loses the DuckLake catalogue;
the S3 objects alone are not a usable lake. A complete disposable reset removes both databases,
NATS, S3 data and sidecar metadata, and the shared repository volume.

The default Compose deployment keeps all persistent state in named volumes, so
`docker compose down --volumes` is a complete reset. A direct-filesystem DuckLake deployment is a
separate production configuration: set `PERIPLUS_DUCKLAKE_DATA_PATH` to an absolute shared path and
mount that path consistently into every Periplus process that writes or reads lake files.

## Environment

The checked-in `.env.example` is the canonical runnable development contract. It separates:

- Compose build inputs and published ports;
- Periplus control Postgres from DuckLake metadata Postgres;
- Versity root credentials from Periplus S3 client credentials;
- host addresses from container-network addresses; and
- required attachment settings from optional storage and operational overrides.

Application code does not infer a DuckLake metadata store or data path.
`PERIPLUS_DUCKLAKE_METADATA_PATH` and `PERIPLUS_DUCKLAKE_DATA_PATH` are required. Standard provider
credentials such as `AWS_*` remain valid where the selected protocol supports them.

The Compose build still compiles the Periplus and DuckLake CDC extensions together against the pinned
DuckDB version. `PERIPLUS_DUCKDB_EXTENSION_REPO` and `PERIPLUS_DUCKLAKE_CDC_EXTENSION_REPO` only relocate
the two additional build contexts; they do not change the extension build.

## Production artifacts

GitHub Actions publishes three immutable artifacts for each main-branch revision:

- `ghcr.io/ekkuleivonen/periplus-backend:sha-<commit>` contains the API, every worker role,
  `periplus-setup`, the Periplus DuckDB extension, and the DuckLake CDC extension;
- `ghcr.io/ekkuleivonen/periplus-web:sha-<commit>` contains the static console and its nginx API
  proxy; and
- `oci://ghcr.io/ekkuleivonen/periplus-charts/periplus:0.1.0-dev.<commit>` deploys the two images.

Release tags `vX.Y.Z` additionally publish matching `X.Y.Z` image and chart versions. Production
GitOps must pin the explicit chart version and both explicit image tags; it must not consume a
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
materializer deployments, and scalable stateless web replicas.

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
