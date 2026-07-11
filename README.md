# Atlas

Atlas is a Python web crawling and search backend. It exposes the same action logic through:

- a FastAPI HTTP API,
- a Typer CLI.

Its current user-facing web actions are `search`, `index`, `crawl`, and `extract`.
`extract` can produce structured data with a `DataSchema`, query parameter affordances with a
`QuerySchema`, or both in one crawl.

The target architecture is documented in [ARCHITECTURE.md](ARCHITECTURE.md). The repository,
DuckLake, raw HTML, and state-ownership contract lives in [STORAGE.md](STORAGE.md).

## Repository Layout

```text
backend/api/          FastAPI app and routers
backend/cli/          Typer CLI commands
backend/actions/      Primitive actions that take inputs and produce outputs
backend/actions/shared/
                      Crawler, quality, progress, data-schema, and query-schema support
backend/repository/   Raw objects, DuckLake catalogue, ingestion, reads, and maintenance
backend/dom/          Versioned loss-minimized DOM schema and encoder
backend/repository/   Raw object storage, cache resolution, and catalogue ingestion
backend/tasks/        Schedulable task definitions and frozen run execution
backend/db/           Postgres setup, SQLAlchemy base/session, and Alembic
docker/atlas/         Backend container image
docker-compose.yml    Local API, worker, Postgres, and NATS stack
```

## Requirements

- Docker and Docker Compose for the full local stack.
- `uv` for local backend development.
- Python 3.14, matching `backend/pyproject.toml`.

## Local Development

Install backend dependencies:

```sh
cd backend
uv sync
```

Run the API locally:

```sh
cd backend
uv run fastapi dev api/app.py
```

Run the CLI:

```sh
cd backend
uv run atlas --help
uv run atlas crawl https://example.com
uv run atlas index https://example.com --max-depth 1
uv run atlas schema https://example.com --prompt "Extract article cards with title and URL."
uv run atlas extract https://example.com --prompt "Extract the main heading and visible links."
uv run atlas extract https://example.com --no-data --query-params
uv run atlas extract "https://jp.mercari.com/en/search?keyword=16tb%20ironwolf" \
  --mode app --wait stable \
  --prompt "Extract each product listing with name, availability status, and price."
```

Run a lightweight syntax check:

```sh
make check
```

Run database migrations:

```sh
make db-upgrade
cd backend && uv run alembic -c db/alembic.ini current
```

The DuckLake-only cutover migration refuses to drop populated legacy crawl/artifact tables by
default. For an existing pre-cutover installation, stop Atlas, export or back up the control-plane
database, review the reported row counts, and rerun once with
`ATLAS_ALLOW_LEGACY_STORAGE_DROP=true`. Clean installations require no acknowledgement.

## Docker Compose

Start the API, worker, Postgres, and NATS:

```sh
docker compose up --build
```

The API is published at `http://127.0.0.1:8000`. Postgres is published at
`127.0.0.1:5432` by default and persists data in the `atlas-postgres-data`
Compose volume. NATS is published at `127.0.0.1:4222` and persists JetStream data in
`atlas-nats-data`.

## Configuration

Copy `.env.example` to `.env` when local secrets are needed.

`DATABASE_URL` points Atlas at Postgres. Compose injects an internal URL for
the API container using `atlas-postgres`; local tools can use the localhost URL
from `.env.example`.

SQLAlchemy models should inherit from `db.Base`. Task-owned tables live in
`tasks/models.py`, while Pydantic contracts live in `tasks/schemas.py`.
Action inputs/outputs live in `actions/<name>/schemas.py`. Alembic reads model metadata from
`backend/db/alembic/env.py`, so create schema changes with:

```sh
make db-revision m="describe change"
make db-upgrade
cd backend && uv run alembic -c db/alembic.ini check
```

`OPENROUTER_API_KEY` is only needed when Atlas must generate a data schema, query schema, or infer a target JSON example. Page-load-sensitive commands support `--mode` and `--wait`; use `--mode app --wait stable` for pages that need frontend hydration before their data appears.

## Storage Direction

Atlas uses a managed repository:

```text
Postgres       control-plane database + separate DuckLake metadata database
JetStream      ingestion jobs, recent progress events, cancellation, and worker state
DuckLake       durable crawls, documents, and DOM elements
filesystem/S3  content-addressed raw HTML.zst and optional debug media
```

Local development and S3-backed deployments use the same relative repository layout. Raw HTML is
the canonical source; DuckLake owns durable analytical Parquet and the SQL query surface. Temporary
page-local DOM Parquet is removed after ingestion. Atlas has no Postgres `artifacts` or
`task_run_artifacts` tables.

The crawl-storage cutover is complete: new captures, cache lookup, structural results, and crawl
history use the repository and DuckLake exclusively. There is no Postgres artifact, crawl, or URL
history compatibility path.

### Catalogue bootstrap

The catalogue uses the published `ducklake-client` package. DuckLake metadata lives in a separate
Postgres database named `atlas_catalogue`; DuckLake data remains on disk or S3. Initialize schema
once as a deployment operation, then validate it independently:

```sh
cd backend
uv run python -m repository.ducklake bootstrap
uv run python -m repository.ducklake check
```

Compose creates `atlas_catalogue` idempotently and runs the bootstrap service before workers.
Runtime workers attach and validate the existing schema; they do not perform concurrent catalogue
DDL. Postgres is the default catalogue backend, so non-Compose processes must provide
`ATLAS_CATALOGUE_CATALOG_DSN`. Relevant configuration:

```text
ATLAS_CATALOGUE_ROOT
ATLAS_CATALOGUE_ALIAS
ATLAS_CATALOGUE_SCHEMA
ATLAS_CATALOGUE_CATALOG=duckdb|sqlite|postgres
ATLAS_CATALOGUE_CATALOG_PATH
ATLAS_CATALOGUE_CATALOG_DSN
ATLAS_CATALOGUE_DATA_INLINING_ROW_LIMIT
ATLAS_CATALOGUE_OVERRIDE_DATA_PATH
```

DuckDB runtime limits can be set with `ATLAS_CATALOGUE_DUCKDB_THREADS`,
`ATLAS_CATALOGUE_DUCKDB_MEMORY_LIMIT`, `ATLAS_CATALOGUE_DUCKDB_MAX_TEMP_SIZE`, and
`ATLAS_CATALOGUE_DUCKDB_TEMP_DIRECTORY`. Atlas disables DuckLake data inlining by default so
crawl/document/element rows remain in the data path rather than growing the Postgres metadata
catalogue. The dedicated `atlas-ingestor` service commits row/byte/item-bounded microbatches.
DuckDB and SQLite catalogue modes
remain available for isolated development and tests, but the Atlas worker topology uses Postgres.

Disk catalogues default `ATLAS_CATALOGUE_OVERRIDE_DATA_PATH=true` so host processes and Compose
containers can attach through different path spellings to the same bind-mounted repository. Set
it to `false` in deployments where a path mismatch should be fatal. S3 paths are stable and do not
enable the override by default.

Run the opt-in multiprocess catalogue integration test against a Postgres server with:

```sh
make catalogue-test-postgres
```

### Raw object store

`backend/repository/` provides the shared raw-object contract and canonical HTML repository.
Captured HTML is UTF-8 encoded, identified by the SHA-256 of those uncompressed bytes, compressed
with Zstandard, and stored with an atomic/conditional create under:

```text
raw/html/sha256/<first-2>/<next-2>/<sha256>.html.zst
```

Configure the deployment with `ATLAS_REPOSITORY_STORAGE=disk|s3`. This single selection controls
both canonical raw objects and DuckLake data, preventing the two durable stores from silently
landing on different backends. Disk uses `ATLAS_REPOSITORY_ROOT` (default `.atlas/repository`),
with DuckLake data under `lake/`. S3 uses
`ATLAS_REPOSITORY_S3_BUCKET`, `ATLAS_REPOSITORY_S3_PREFIX`,
`ATLAS_REPOSITORY_S3_ENDPOINT`, `ATLAS_REPOSITORY_S3_REGION`, credentials, and
`ATLAS_REPOSITORY_S3_URL_STYLE`/`ATLAS_REPOSITORY_S3_USE_SSL`; DuckLake data is placed under
`<prefix>/lake`. Standard AWS region and credential variables are supported as fallbacks. Raw
object keys remain relative and identical across backends.

Repository cache freshness defaults to 120 seconds through `ATLAS_CACHE_MAX_AGE_SECONDS`.
`ATLAS_CACHE_STALE_IF_ERROR_SECONDS` optionally enables bounded stale fallback after network
failure. Requests can override matching CrawlPolicy defaults with `cache.mode` (`prefer`,
`refresh`, or `no_store`), `cache.max_age_seconds`, and `cache.stale_if_error_seconds`. CLI actions
expose the same behavior through `--refresh`, `--no-store`, `--max-cache-age`, and
`--stale-if-error`.

Compose includes MinIO at `http://127.0.0.1:9000`, with its console at
`http://127.0.0.1:9001`. The `atlas-minio-init` one-shot service idempotently creates the local
development bucket. External S3 buckets remain operator-managed. Run the S3 contract tests with:

```sh
make repository-test-s3
```

### Repository ingestion and bounded indexing

Task workers stream canonical HTML compression to disk/S3, publish frozen crawl provenance to the
`ATLAS_REPOSITORY` JetStream work queue, and release their HTML reference before waiting. The
dedicated `atlas-ingestor` process parses DOM, writes bounded Arrow/Parquet batches, and is the only
runtime DuckLake writer. Run it outside Compose with:

```sh
make ingestor
```

Per-document safety limits use `ATLAS_REPOSITORY_MAX_HTML_BYTES`,
`ATLAS_REPOSITORY_MAX_DOCUMENT_ELEMENTS`, and
`ATLAS_REPOSITORY_MAX_DOCUMENT_STAGED_BYTES`. Ingestion batching and redelivery use the
`ATLAS_INGEST_*` variables documented in `.env.example`.

Repository ingestion state is durable in the bounded `ATLAS_REPOSITORY_RESULTS` JetStream KV
bucket. Producers create a stable pending operation before publishing, record PubAck with KV CAS,
and poll KV for terminal state. Recovery republishes only when publication was not confirmed.
Inbox replies only reduce
latency; workers persist success/failure before ACK/TERM, so a disconnected or replaced producer
can resume without reacquiring the page.

After configured redeliveries, terminal ingestion failures are retained in the file-backed
`ATLAS_REPOSITORY_DEAD_LETTER` stream. Use `atlas repository dead-letters` to inspect them and
`atlas repository requeue <sequence>` to reset and republish the original frozen job through the
Atlas operations API. The
ingestor `/healthz` endpoint returns ready only while its event-loop heartbeat, NATS connection,
and catalogue validation remain healthy.

Index traversal is a bounded in-memory breadth-first walk. `max_pages` and `max_links` are hard
run limits, and returned links are capped by `ATLAS_INDEX_RESULT_LIMIT`.
