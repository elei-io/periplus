# Deployment

Atlas deployment names distinguish infrastructure authorities, executable process roles, and
domain capabilities.

## Naming

- Infrastructure uses ownership-first names: `lake-s3`, `lake-postgres`, `atlas-postgres`, and
  `atlas-nats`.
- Executable roles use actor names: `atlas-crawler`, `atlas-ingestor`, `atlas-materializer`, and
  `atlas-janitor`.
- Domain names remain `crawl`, `acquisition`, `ingestion`, and `materialization` in schemas,
  queues, APIs, metrics, and capability packages.

The crawler, ingestor, and materializer are horizontally scalable and must not declare a Compose
`container_name`. Singleton infrastructure, API, web, janitor, and setup services have explicit
container names. This means one local Atlas Compose project may run on a Docker host at a time.

## Local topology

| Service | Authority or role | Persistent state |
| --- | --- | --- |
| `lake-s3` | S3-compatible DuckLake object storage | `lake-s3-data` and `lake-s3-metadata` |
| `lake-postgres` | DuckLake metadata only | `lake-postgres-data` |
| `atlas-postgres` | Atlas editable control and current execution state only | `atlas-postgres-data` |
| `atlas-nats` | JetStream/KV delivery, presence, pacing, and leases | `atlas-nats-data` |
| `atlas-crawler` | Crawl graph execution and page acquisition | shared `atlas-repository-data` |
| `atlas-ingestor` | Immutable crawl and visit evidence writes | shared `atlas-repository-data` |
| `atlas-materializer` | Fixed projections, rebuilds, and live CDC | shared `atlas-repository-data` |
| `atlas-api` | HTTP API | shared `atlas-repository-data` |
| `atlas-web` | Web application | none |
| `atlas-janitor` | Atlas-owned staging, navigation, and runtime cleanup | shared `atlas-repository-data` |
| `atlas-setup` | One-shot schema and catalogue installation | shared `atlas-repository-data` |

`ATLAS_CONTROL_DATABASE_URL` always identifies Atlas Postgres.
`ATLAS_DUCKLAKE_METADATA_PATH` independently identifies the DuckLake metadata store. They must
never target the same database in the maintained Compose deployment.

Deleting `atlas-postgres-data` loses editable plans, schedules, policies, and current execution
state without deleting lake history. Deleting `lake-postgres-data` loses the DuckLake catalogue;
the S3 objects alone are not a usable lake. A complete disposable reset removes both databases,
NATS, S3 data and sidecar metadata, and the shared repository volume.

The default Compose deployment keeps all persistent state in named volumes, so
`docker compose down --volumes` is a complete reset. A direct-filesystem DuckLake deployment is a
separate production configuration: set `ATLAS_DUCKLAKE_DATA_PATH` to an absolute shared path and
mount that path consistently into every Atlas process that writes or reads lake files.

## Environment

The checked-in `.env.example` is the canonical runnable development contract. It separates:

- Compose build inputs and published ports;
- Atlas control Postgres from DuckLake metadata Postgres;
- Versity root credentials from Atlas S3 client credentials;
- host addresses from container-network addresses; and
- required attachment settings from optional storage and operational overrides.

Application code does not infer a DuckLake metadata store or data path.
`ATLAS_DUCKLAKE_METADATA_PATH` and `ATLAS_DUCKLAKE_DATA_PATH` are required. Standard provider
credentials such as `AWS_*` remain valid where the selected protocol supports them.

The Compose build still compiles the Atlas and DuckLake CDC extensions together against the pinned
DuckDB version. `ATLAS_DUCKDB_EXTENSION_REPO` and `ATLAS_DUCKLAKE_CDC_EXTENSION_REPO` only relocate
the two additional build contexts; they do not change the extension build.

## Process entrypoints

The shared Atlas runtime image exposes:

```sh
atlas-worker crawler
atlas-worker ingestor
atlas-worker materializer
atlas-worker janitor
```

Process-specific configuration follows the same actor names:
`ATLAS_CRAWLER_*`, `ATLAS_INGESTOR_*`, `ATLAS_MATERIALIZER_*`, and
`ATLAS_JANITOR_*`. Capability contracts such as ingestion subjects and materialization metrics
retain their domain terminology.

The maintained development replica baseline is one crawler, four ingestors, and four
materializers. `ATLAS_INGESTOR_CONCURRENCY` and `ATLAS_MATERIALIZER_CONCURRENCY` remain
per-process lane bounds; replica count and local concurrency are separate controls.
