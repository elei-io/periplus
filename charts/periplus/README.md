# Periplus Helm chart

This chart deploys the Periplus API, admin application, public application, crawler, ingestors, materializers, janitor, and an
idempotent setup hook. It intentionally does not deploy PostgreSQL, NATS, S3, a CDP service, an
Ingress, or secret-management controllers; those remain platform-owned dependencies.

Every backend role uses one image tag. That image contains Periplus and DuckDB storage extensions plus the signed CDC community
package, with its version and source revision verified by
`packages/periplus/src/periplus/platform/catalogue/cdc_extension.py`.
`periplus-setup` runs as a blocking `pre-install,pre-upgrade` hook, so catalogue and control schema
setup succeeds before Kubernetes rolls any runtime to the new image.

## Required platform contract

Provide seven Kubernetes Secrets using `secrets.*` values:

| Reference | Required key | Meaning |
| --- | --- | --- |
| `controlDatabase` | `DATABASE_URL` | Standard SQLAlchemy PostgreSQL URL for editable Periplus state |
| `ducklakeMetadata` | `DATABASE_URL` | Standard PostgreSQL URL for the distinct DuckLake metadata database |
| `nats` | `NATS_URL`, `NATS_SEED` | Periplus NATS namespace credentials |
| `s3` | `ENDPOINT`, `REGION`, `BUCKET`, `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY` | Shared S3-compatible bucket credentials |
| `apiAccess` | `ADMIN_API_TOKEN`, `PUBLIC_API_TOKEN`, `QUERY_API_TOKEN` | Distinct service tokens |
| `queryDucklakeMetadata` | `DATABASE_URL` | Dedicated metadata reader |
| `queryS3` | `REGION`, `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY` | GetObject-only lake reader |

The control and DuckLake metadata stores must be separate PostgreSQL databases. The same S3 bucket
can hold immutable raw HTML under `config.repository.prefix` and DuckLake files under the path in
`config.ducklake.dataPath`.

The platform S3 `ENDPOINT` value is passed directly to boto3 for the raw repository and should keep
its `http://` or `https://` scheme, matching `config.repository.s3UseSSL`. DuckDB's S3 secret syntax instead requires `config.ducklake.s3Endpoint` as
`host:port` without a scheme; `config.ducklake.s3UseSSL` selects HTTPS.

The homelab already declares separate `periplus_control` and `periplus_lake` databases.
Preserve their identities. Provision the chart-shaped Secrets in `applications`; the database-owner
Secrets in `state` are not application connection bundles. See [homelab onboarding](../../docs/HOMELAB.md)
for initial values, grants, capacity, recovery, and acceptance checks.

## Flux example

This shows source/release wiring. Merge the full [initial values](examples/homelab-values.yaml)
into the release; the abbreviated values below are not the conservative onboarding profile.

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: periplus-chart
  namespace: applications
spec:
  interval: 10m
  url: oci://ghcr.io/elei-io/periplus-charts/periplus
  secretRef:
    name: ghcr-pull
  ref:
    tag: 0.1.0-dev.abcdef0
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: periplus
  namespace: applications
spec:
  interval: 10m
  timeout: 20m
  chartRef:
    kind: OCIRepository
    name: periplus-chart
  values:
    imagePullSecrets:
      - name: ghcr-pull
    images:
      core:
        tag: sha-abcdef0
      admin:
        tag: sha-abcdef0
      public:
        tag: sha-abcdef0
    config:
      cdpUrl: ws://stolosio-api.stolosio.svc:8411/v1/connect
      repository:
        s3UseSSL: false
      ducklake:
        dataPath: s3://periplus/ducklake/
        s3Endpoint: 192.168.40.101:7070
        s3UseSSL: false
```

The Periplus repository and its GHCR packages are private by default, so both Flux's OCI source and
Periplus Pods need a `kubernetes.io/dockerconfigjson` pull secret such as the homelab's `ghcr-pull`.
This can be removed if the published packages are made public.

All five metrics endpoints are exposed through annotated Services for the homelab Alloy discovery
contract. The API Service exposes `/metrics`; worker Services expose ports 9090, 9091, 9093, and
9094.

The three image keys are `images.core`, `images.admin`, and `images.public`; pin all three.
Create the externally managed `periplus-api-access` secret with distinct `ADMIN_API_TOKEN`,
`PUBLIC_API_TOKEN`, and `QUERY_API_TOKEN` keys. Configure alternative names through `secrets.apiAccess`.
Route public and admin through separate TLS ingresses. Protect the entire admin hostname, including its proxied
`/api` routes, with Cloudflare Access; admin has no built-in login. Restrict origin access
to that protected ingress path. Keep the core API private and enforce aggregate public
rate limits at ingress. No database or service token is supplied to the public browser.

The `query` deployment runs preparation and execution separately from the control API. Public proxies query requests using `secrets.apiAccess.queryTokenKey`. Admin SQL uses
the control API and `secrets.apiAccess.adminTokenKey` for writable DuckLake access. Provision separate
`secrets.queryDucklakeMetadata` (SELECT-only, including future metadata tables) and
`secrets.queryS3` (GetObject-only on the lake prefix). Query does not inherit shared extraEnv
or extraEnvFrom; do not inject a writer identity through pod credentials. See docs/QUERY.md
for execution and response limits. This chart does not provision those external grants.

## Autoscaling and capacity

Every scalable Deployment (`api`, `admin`, `public`, `crawler`, `ingestor`,
`materializer`, `query`) has an `autoscaling` block. It is disabled by default;
fixed `replicas` remain effective until enabled. A fixed crawler may use zero replicas
for initial provisioning before setting global frontier controls; KEDA minimum replicas remain positive. Janitor stays one replica with
Recreate deployment, and setup stays a blocking one-shot Job. API instances own
bounded local clients and request admission slots; durable control transitions
remain in PostgreSQL and operation-scoped leases. More API replicas multiply
those local limits, so ingress still owns aggregate admission/rate limits.

The platform must install **KEDA 2.18+**, a reachable Prometheus-compatible query
endpoint, and metrics-server for CPU triggers. The chart does not install those
services. `examples/autoscaling.yaml` enables all seven scalable roles and shows
resource budgets. Replace its Prometheus endpoint, configure scraping, then merge
it into the release's existing image, storage, and secret values. The example
uses the optional Prometheus Operator PodMonitors; set `podMonitor.labels` to match
the platform Prometheus `podMonitorSelector`. Do not enable PodMonitors without
the Prometheus Operator CRDs. An Alloy/external collector can be used instead.

| Role | Default signal | Target per replica | Default min–max when enabled |
| --- | --- | --- | --- |
| Crawler | Two-minute average authorized capture occupancy | 8.4 of 12 slots | 2–10 |
| Ingestor | Pending + unacknowledged jobs in shared durable consumer | 200 jobs | 2–10 |
| Materializer | Pending + unacknowledged visit-batch messages | 4 batches | 2–10 |
| Query | Two-minute average occupied SQL admission slots | 0.7 of 1 slot | 2–10 |
| API | CPU utilization relative to CPU request | 70% | 2–6 |
| Public | CPU utilization relative to CPU request | 70% | 2–8 |
| Admin | CPU utilization relative to CPU request | 70% | 2–4 |

These are initial tuning values, not measured throughput guarantees. Queue
policies use `AverageValue`: desired replicas are approximately total outstanding
work divided by target work per replica, clamped to min/max. Queue observations
are repeated across replicas and aggregated with `max`, never summed across
replicas. Materialization sums the separate pending and unacknowledged states
only after deduplication. Unacknowledged work prevents an empty delivery queue
from appearing idle while commits are in progress.

Crawler occupancy starts only after domain permits, pacing, dependency checks,
and physical-attempt authorization. A blocked hostname or paused frontier does
not inflate desired replicas. This conservative policy maintains capture
headroom; it does not scale on unresolved discovery/selection backlog or prove
unused browser capacity. Keep `crawler.autoscaling.maxReplicas * 12` within the
intended CDP budget, allowing rollout surge and terminating pods. Global crawler
controls and per-domain limits remain authoritative.

Query measures admitted SQL work, including preparation and connection recovery;
it does not count rejected/unauthenticated requests as occupied SQL slots.
Scaling provides more independent one-request DuckDB processes, not distributed
execution of a single query. CPU-based frontend scaling does not directly measure
I/O-bound assistant runs; use a custom Prometheus query for that signal if needed.

`<role>.autoscaling.metric` selects `prometheus` or `cpu`;
`<role>.autoscaling.threshold` is a positive string (an integer for CPU).
`<role>.autoscaling.query` optionally replaces the built-in PromQL and is evaluated
as a Helm template. Prometheus queries must return one nonnegative total demand
value, not a per-pod average; KEDA divides the total by the per-replica threshold.
For CPU policies, `<role>.resources.requests.cpu` is required.

Built-in queries require `namespace`, `periplus_release` (Helm release name), and
`periplus_component` labels. PodMonitors attach these labels and discover every pod
individually. External collectors must attach the same labels and scrape each
pod, not a load-balanced Service address. Use a datasource scoped to one cluster
or supply cluster-scoped custom queries if namespaces/releases repeat across
clusters. Avoid duplicate collection paths for the same pod: occupancy metrics
are additive, unlike shared queue observations.

Samples/queue observations older than 60 seconds are excluded. Missing metrics
are errors (`ignoreNullValues: false`), not zero demand. There is no fixed-replica
fallback that could downscale a busy fleet during monitoring loss; KEDA/HPA holds
on metric errors. Keep scrape intervals below the freshness bound (15s by default).
Watch KEDA/HPA conditions for missing/invalid metrics.
`autoscaling.prometheus.authenticationRef` accepts an existing KEDA authentication
reference; `metadata` accepts extra scaler metadata such as `authModes`. Keep
credentials in the referenced platform secret, not chart values.

`autoscaling.behavior` supplies the default HPA behavior: immediate scale-up at
up to 100% or two pods per minute, and scale-down by at most one pod per minute
after a five-minute stabilization window. A nonempty
`<role>.autoscaling.behavior` replaces that complete behavior. Scale-to-zero is
not supported: workers also own scheduling, recovery, CDC election, and the
telemetry used to make scaling decisions. Min/max and sizing values are validated
by Helm. Do not attach another HPA to these Deployments.

When autoscaling is enabled the Deployment omits `spec.replicas` so Helm/GitOps
does not continuously reset KEDA's desired count. API/query roll with zero
unavailable pods and one surge pod. The chart's configurable termination grace
is 330 seconds, allowing bounded native catalogue work to drain. Expiry still
allows Kubernetes to kill a pod; durable work can be redelivered. Max replicas
bounds desired replicas, not temporary rollout/termination overlap. Account for
that overlap in platform capacity budgets.

## Resource and DuckDB limits

Every role, including janitor/setup, exposes the full Kubernetes `resources`
map: `requests` and `limits` for CPU, memory, and optional `ephemeral-storage`.
CPU/memory limits are explicit in the defaults. `ingestor.concurrency` (1–4) and
`materializer.concurrency` are per-process writer/batch lane counts, separate
from replica count. Raising limits alone does not change these lane counts.

Every Python role also exposes:

```yaml
query:
  resources:
    requests: {cpu: "1", memory: 1Gi}
    limits: {cpu: "2", memory: 2Gi, ephemeral-storage: 2Gi}
  duckdb:
    threads: 2
    memoryLimit: 1GB
    maxTempDirectorySize: 512MB
```

These map to `PERIPLUS_DUCKDB_THREADS`, `PERIPLUS_DUCKDB_MEMORY_LIMIT`, and
`PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE` in that role only. They override
connection defaults at the managed connection factory, including materializer
planning, activation and CDC, API/admin/storage clients, and crawler standalone
follow SQL. Read-only query credentials and locked configuration remain intact.
Memory requires a positive integer size with B/KB/MB/GB/TB/PB or binary units;
spill accepts zero (`0B`) to disable spilling. Threads must be 1–1024. Outside
Helm, unset settings retain each caller's defaults.

**Budgets are per connection, not per pod.** An ingestor has up to four writer
connections; a materializer can run batch connections alongside planning,
activation, recovery, and CDC connections. Leave additional pod memory for Python,
HTML parsing, Arrow/Parquet buffers, browser clients, and native allocations
outside DuckDB's managed budget. Spill limits also multiply across connections;
`resources.limits.ephemeral-storage` must allow spill, temporary files, logs and
writable-layer overhead. Tune lane count and replica maxima against PostgreSQL
connection/commit capacity, NATS delivery bounds, S3 throughput and CDP capacity.
Autoscaling cannot resolve a failed coordinator or shared-store contention.

Validate with `helm lint charts/periplus` and `helm template` using the example.
The backend test suite renders default, enabled, invalid and custom policies,
checks credential isolation, and tests limits against a real DuckDB connection.
Platform acceptance should exercise backlog growth/drain, query saturation,
monitoring loss, and SIGTERM of a busy worker before enabling production scaling.

References: [KEDA ScaledObject](https://keda.sh/docs/2.18/reference/scaledobject-spec/),
[Prometheus scaler](https://keda.sh/docs/2.18/scalers/prometheus/),
[Kubernetes HPA behavior](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/).

## NATS replication

`config.nats.operationalReplicas` (1–5, default 3) controls crawl delivery,
worker presence, domain pacing and operation leases together. Catalogue work/dead
letters use `catalogueWorkStreamReplicas`; ingestion results use
`ingestResultReplicas`. All three default to 3 in Helm. The local runtime default
for `PERIPLUS_NATS_OPERATIONAL_REPLICAS` is 1 for single-server development.
Every process sharing an account must use matching settings. Replica mismatch
fails startup; changing values alone does not migrate existing streams. See
[onboarding](../../docs/HOMELAB.md#nats-storage-and-failure-contract) for account
sizing and the explicit, data-preserving upgrade procedure.

## Query execution modes

The chart runs stable (`query`) and experimental (`queryExperimental`) query
services independently with the same core image and read-only credentials.
`queryExperimental.replicas`, `.resources`, and `.duckdb` control experimental
capacity; it uses fixed replicas. Pod monitoring includes both modes. The public
application receives both internal URLs and offers the mode selector in the SQL
console. See [query semantics](../../docs/QUERY.md#stable-and-experimental-execution).
