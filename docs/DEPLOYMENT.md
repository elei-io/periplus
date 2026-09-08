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
| `periplus-postgres` | Periplus control, execution, retirement and materialization state | `periplus-postgres-data` |
| `periplus-nats` | JetStream/KV delivery, presence, pacing, and leases | `periplus-nats-data` |
| `periplus-crawler` | Shared frontier execution, page acquisition, and immutable raw-object writes | S3 `raw` namespace |
| `periplus-ingestor` | Immutable crawl and visit evidence writes | none |
| `periplus-materializer` | Fixed projections, rebuilds, and live CDC | S3 `raw` reads and `lake` writes |
| `periplus-query` | Isolated read-only SQL preparation and execution | none |
| `periplus-api` | Control HTTP API | S3 `raw` namespace |
| `periplus-admin` | Operator application and API gateway | none |
| `periplus-public` | Public Next.js application and bounded API client | none |
| `periplus-janitor` | Transient cleanup; opt-in logical retention and raw-object reclamation | Postgres current roots, DuckLake writer, NATS operation leases, S3 `raw` namespace |
| `periplus-setup` | One-shot schema and catalogue installation | none |

Periplus requires PostgreSQL 17+ for control and metadata connections. Worker-owned
connections enforce a 240-second server transaction timeout as part of bounded write
ownership. This does not change LakeDucktor-owned connection settings.

`PERIPLUS_CONTROL_DATABASE_URL` always identifies Periplus Postgres.
`PERIPLUS_DUCKLAKE_METADATA_PATH` independently identifies the DuckLake metadata store. They must
never target the same database in the maintained Compose deployment.
For platform integration, `PERIPLUS_DUCKLAKE_METADATA_PATH` accepts either DuckLake's native
`postgres:...` attach form or a standard `postgresql://...`/`postgres://...` URL. Periplus normalizes
standard URLs to the native DuckLake form at its configuration boundary.

Deleting `periplus-postgres-data` loses collections, crawler controls, policies, and current execution
state, retirement tombstones, deletion queues and generation/batch receipts without deleting
lake history. Preserve control Postgres with the lake when backing up or restoring;
losing tombstones would invalidate delayed-job retirement protection. Deleting `lake-postgres-data` loses the DuckLake catalogue;
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

The Compose build installs the signed DuckLake CDC community package without compiling DuckDB
or requiring a sibling checkout. Query connections use standard DuckDB and official storage
extensions; only live materialization loads CDC.

## Production artifacts

GitHub Actions publishes four immutable artifacts for each main-branch revision:

- `ghcr.io/ekkuleivonen/periplus-core:sha-<commit>` contains the API, every worker role,
  `periplus-setup`, the DuckLake CDC extension;
- `ghcr.io/ekkuleivonen/periplus-admin:sha-<commit>` contains the operator UI and nginx API gateway;
- `ghcr.io/ekkuleivonen/periplus-public:sha-<commit>` contains the standalone Next.js public application; and
- `oci://ghcr.io/ekkuleivonen/periplus-charts/periplus:0.1.0-dev.<commit>` deploys the three images.

Release tags `vX.Y.Z` additionally publish matching `X.Y.Z` image and chart versions. Production
GitOps must pin the explicit chart version and all three explicit image tags; it must not consume a
mutable `latest` tag.

The core image installs official storage extensions and signed community CDC at build time.
`packages/periplus/src/periplus/platform/catalogue/cdc_extension.py` validates the pinned
DuckDB version, CDC version, and source revision during build and CDC startup. No extension
source checkout, BuildKit named context, deploy key, or unsigned loading is required.

## Kubernetes topology

The chart under `charts/periplus` owns only Periplus processes. PostgreSQL, NATS JetStream, S3-compatible
storage, the standard CDP endpoint, secret projection, ingress, and metrics storage remain external
platform authorities. The chart supports scalable API, query, crawler, ingestor, materializer, admin and
public deployments, plus one janitor and a one-shot setup Job.

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

### Public search metadata

`PERIPLUS_PUBLIC_ORIGIN` is a **build-time**, non-secret HTTPS origin for the public
application. It must contain no path, query, fragment, or credentials. Set the
GitHub Actions repository variable of that name before publishing the public
image; the frontend build passes it to `docker/public/Dockerfile`. For manual
Docker builds, pass `--build-arg PERIPLUS_PUBLIC_ORIGIN=https://your-public-host`.
For host builds, export it before `npm run build --workspace periplus-public`.

The origin is baked into prerendered canonical URLs, social metadata, robots.txt,
and sitemap.xml. Changing only a Kubernetes runtime environment value does not
update those files: rebuild the public image for an origin change. An unset origin
keeps the build usable locally but emits `noindex`, a disallow-all robots file,
and an empty sitemap. Never infer canonical URLs from request Host headers.
Preview deployments of an indexable production image need their own ingress
authentication or `X-Robots-Tag: noindex`; robots rules are not an access control.

Only the six clean public page URLs enter the sitemap. SQL, question, and request
parameter variants and transient observation details are noindex; the clean
workspace pages remain indexable. Before launch, verify these files on the actual
TLS host, redirects to that host, and Search Console ownership/sitemap submission.

Core requires distinct `PERIPLUS_ADMIN_API_TOKEN` and `PERIPLUS_PUBLIC_API_TOKEN` values.
Missing or equal credentials fail closed. Only health and Prometheus metrics are anonymous;
keep the API on the private service network. Admin injects the administrative credential
server-side and has no built-in login in development or production. Cloudflare Access owns
production operator authentication and must cover both the UI and all proxied `/api` routes.
Route admin through a separate TLS ingress with origin access restricted to that protected path. Public receives only its restricted token.

The public app receives `PERIPLUS_QUERY_URL` and `PERIPLUS_QUERY_API_TOKEN` for SQL, plus
`PERIPLUS_API_URL` and the restricted `PERIPLUS_PUBLIC_API_TOKEN` for collection submission
and public status listing. Next.js only transports these requests; Python validates and stores
them in Periplus Postgres. Public credentials cannot start crawls. The admin console proxies `/api/admin/sql/exec` to the control API using the admin token
for request-scoped writable SQL. Core uses its separate API tokens.
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
The assistant streams with Vercel AI SDK, bounds exploration by time and output tokens (32 steps and 180 seconds overall),
and admits two active runs per process. Ingress must provide deployment-wide rate and body limits.

## Crawler acquisition and discovery

Collection discovery belongs to the crawler. The API validates and stores intent; it does not
run discovery or dispatch. URL submissions require no model credentials. The CDP service must
prevent private-network egress; initial DNS checks alone do not constrain redirects, subresources,
or DNS rebinding.

When frontier exclusions are configured, page acquisition requires the standard CDP
`Page.getFrameTree` and `Fetch` request-interception commands in addition to navigation. An
endpoint that cannot install the interception must fail the attempt before navigation; there is
no unchecked acquisition fallback. Page-session interception supplements the CDP service's egress
restrictions; it does not establish enforcement for independent workers or other browser targets.

The query process uses its existing API URL and query token to read `GET /access` before each
SQL operation. This is a required dependency for authoritative duration, row and result-size
limits; failed reads reject queries. Apply migration `20260908_0010` before deploying. Public
SQL proxies allow up to 130 seconds; configure ingress timeouts accordingly if raising the
execution duration above its default 20 seconds. No control database credentials enter query pods.

The query service also exposes authenticated `GET /query/helpers` for registry-derived SQL helper documentation. Catalogue setup installs helpers before query processes validate and serve them.

### Crawler corpus selection client

The crawler receives `PERIPLUS_QUERY_URL` and `PERIPLUS_QUERY_API_TOKEN` to execute explicit corpus
seed SQL through the isolated query service. Compose points it at `periplus-query:8000`; Helm uses
the query service's configured port and the existing query-token secret reference. This grants no
query-service write capability and introduces no additional process role. Query unavailability
pauses seed selection with a visible waiting reason; URL seeds and page-local follow selection
remain independent of this dependency.

### Collection source discovery

Description-based collection discovery runs in the crawler alongside seed and follow selection.
The crawler receives `PERIPLUS_DISCOVERY_MODEL`, `OPENAI_API_KEY`, and `BRAVE_SEARCH_API_KEY`;
these credentials are no longer supplied to the control API for discovery. Helm settings live under
`crawler.discovery` (`model`, `existingSecret`, `openaiKeyKey`, and `braveKeyKey`). The model has no
implicit default. Missing provider configuration defers discovery visibly while direct URL collection
continues. The returned model identity is frozen after query planning and reused for source selection.

One pass performs at most one provider request or one DNS validation. Provider responses are capped
at 512 KiB and 25 seconds; the complete asynchronous discovery pass is capped at 30 seconds. Search
plans, completed searches, and source selection live in bounded collection checkpoints. A failed
worker resumes the last committed phase instead of dispatching a synthetic graph run.

### Replacement control baseline

The sole Alembic revision is `20260907_0001`, containing the continuous crawler's ten control tables.
It has no upgrade bridge from graph or coverage schemas. Stop old producers/workers and reset
only the intended disposable development state as part of the coordinated cutover before setup.
Setup installs the frontier singleton and default policies; rerunning it preserves operator controls.
The code replacement alone is not evidence that a deployed environment has been cut over.

Crawler pause prevents new physical authorization; already-started captures finish under their
frozen bounds. It is not a browser kill switch. There is no force-abort API in this cutoff.
For an emergency, stop outbound access at the CDP deployment/network boundary; stopping a Periplus
worker alone cannot prove that a remote browser stopped. Lost in-flight outcomes are recovered as
uncertain attempts within the configured attempt/time allowances, not reported as cancelled network I/O.

### Request retention

[RETENTION.md](RETENTION.md) defines the four `PERIPLUS_RETENTION_*` settings,
read-only review command, schema 6.0.0 cutover and physical deletion guarantees.
Retain the singleton janitor. Purge is disabled by default. Enabling it requires
the janitor to access the same lake, control database, operation-lease KV and raw
object store as the writers. Snapshot expiry and registered-file cleanup remain
LakeDucktor operations; raw deletion waits for observed expiry plus reader grace.

### Request schedules

Follow [SCHEDULES.md](SCHEDULES.md) for the `20260908_0004` / `20260908_0005`
control migrations and coordinated worker restart. Scheduling runs in the existing
crawler process; the separate background selection path is removed.

Request-duration intent uses revision `20260908_0006`. Deploy API, crawler, janitor
and ingestors together: the ingestors validate the new `duration_limit` outcome.
The migration removes the unused deadline key from reusable definitions; actual
execution deadlines and immutable crawl evidence are unchanged.

### Operational telemetry

See `observability/README.md` for Prometheus/Loki collection, privacy boundaries,
Grafana dashboard import and alert rules. Workers use `/livez` for startup/liveness
and `/healthz` for dependency readiness. Private query/API `/metrics` endpoints
are unauthenticated; the public application's `/api/metrics` requires the query
service credential and must remain private at ingress. Scrape individual replicas.

## Autoscaling and process budgets

The production chart provides opt-in KEDA autoscaling for API, admin, public,
crawler, ingestor, materializer and query. Each has min/max replica limits and
configurable targets/behavior. Platform-owned KEDA, Prometheus and metrics-server
supply scaling infrastructure; optional PodMonitors supply per-pod discovery and
release-scoped labels. See [the chart guide](../charts/periplus/README.md#autoscaling-and-capacity)
and [complete example](../charts/periplus/examples/autoscaling.yaml).

Crawlers scale on authorized capture occupancy, ingestors/materializers on fresh
shared queue observations, queries on admitted SQL occupancy, and HTTP applications
on CPU by default. Janitor stays singleton and setup remains a hook Job. Autoscaled
Deployments omit fixed replicas; API and query support rolling replicas. Local API
admission limits multiply with replicas; ingress owns aggregate traffic limits.

Each Python role's Helm `duckdb` block configures threads, memory and spill per
connection through `PERIPLUS_DUCKDB_THREADS`, `PERIPLUS_DUCKDB_MEMORY_LIMIT` and
`PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE`. These override individual managed
connection defaults, including coordinator and follow-SQL connections. Pod
`resources` remain independent; allow for all concurrent connections and parsing
buffers. Outside Helm, absent overrides retain the existing workload defaults.
The default pod termination grace is 330 seconds; durable work remains replayable
if a pod is killed before draining. Scaling changes do not alter storage ownership,
leases, domain policies, query isolation or schema contracts.

Public capability controls and the shared-request cutover are documented in [ACCESS.md](ACCESS.md).
They reuse the control API and Postgres; the isolated query service receives no control database
credentials. Gate public queries through Next.js and keep direct service endpoints protected.

### Private query execution history

[QUERY_HISTORY.md](QUERY_HISTORY.md) defines the 30-day private `query_executions`
table in control Postgres, bounded best-effort recording, janitor cleanup and the
`observatory/queries` dashboard. This is explicitly approved product analytics;
no query results or crawl history are added to control Postgres.
