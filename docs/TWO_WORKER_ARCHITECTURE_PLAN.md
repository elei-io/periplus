# Two-Worker Architecture Cutover Plan

Status: implementation-ready target contract.

This plan replaces Atlas's current runtime, repository, materialization, and Quack execution story
with two horizontally scalable worker classes:

```text
crawl-worker
catalog-worker
```

The change is a direct greenfield cutover. Do not add compatibility queues, dual execution paths,
legacy worker aliases, or permanent adapters. Update the frozen contracts, reset disposable runtime
state, and remove the superseded services when each phase is proven.

## Goals

1. **Simplify the architecture story.** One worker acquires pages; one worker turns acquired evidence
   into trustworthy catalogue state and further graph navigation.
2. **Make both planes horizontally scalable.** Crawl capacity and catalogue capacity scale
   independently from their own queue pressure.
3. **Make failures easy to locate and explain.** A crawl is either waiting for acquisition or
   waiting for catalogue processing; each durable operation has one deterministic identity and one
   visible recovery path.
4. **Keep graph navigation trustworthy.** Navigation-critical projections, especially
   `page_links`, must keep pace with acquisition and become durable before outgoing edges run.
5. **Remove correctness-critical dependence on Quack.** Embedded DuckDB/DuckLake connections in
   catalog workers replace remote Quack connections, connection IDs, and the central compute
   bottleneck.

## Non-goals

- Turning crawl graphs into a general workflow engine.
- Moving current graph-run state into Postgres or DuckLake.
- Letting crawl workers evaluate edge SQL or mutate DuckLake.
- Scaling according to graph depth. Queue depth, oldest work age, throughput, and saturation are the
  useful signals; topology depth is not.
- Retaining the present worker layout as a fallback.

## Verified DuckLake capabilities

DuckLake data inlining stores small inserts and deletes in the metadata catalogue rather than
creating one Parquet file per change. `data_inlining_row_limit` can be persisted globally, per
schema, or per table. A table-specific value takes precedence over connection and global defaults:

```sql
CALL atlas.set_option(
  'data_inlining_row_limit',
  16000,
  schema => 'main',
  table_name => 'elements'
);
```

Inlined rows remain ordinary DuckLake data with snapshot and time-travel semantics. They can later
be flushed for one table:

```sql
CALL ducklake_flush_inlined_data(
  'atlas',
  schema_name => 'main',
  table_name => 'elements'
);
```

DuckLake also has transaction-conflict retry settings. Atlas's local `ducklake-cdc` end-to-end
suite already exercises five concurrent writers with a Postgres catalogue; embedded DuckDB and
SQLite catalogues are excluded from that scenario because their catalogue locking is less suitable.

References:

- [DuckLake data inlining](https://ducklake.select/docs/stable/duckdb/advanced_features/data_inlining)
- [DuckLake configuration](https://ducklake.select/docs/stable/duckdb/usage/configuration)
- [Local concurrent-writer evidence](../../quack/ducklake-cdc-extension/e2e/README.md)

These capabilities mean the Atlas-wide single-writer rule is a policy to replace, not a DuckLake
requirement. The replacement is per-operation idempotency, bounded transaction retries, and a
narrow maintenance lease.

## Target architecture

```text
API / schedule / graph edge
             |
             v
      NATS CrawlRequest
             |
             v
     +------------------+
     |   crawl-worker   |  horizontally scalable
     |------------------|
     | reusable HTTP    |
     | long-lived       |
     | Chromium         |
     | CrawlPolicy      |
     +--------+---------+
              |
              | immutable raw HTML + frozen ingestion job
              v
     +------------------+
     |  catalog-worker  |  horizontally scalable
     |------------------|
     | embedded DuckDB  |
     | DuckLake writes  |
     | DOM projection   |
     | page_links       |
     | materializations |
     | edge SQL         |
     | crawl readiness  |
     +--------+---------+
              |
              +------> NATS CrawlRequests selected by graph edges
              |
              +------> DuckLake crawl history and derived evidence
```

State ownership does not change:

| Owner | State |
| --- | --- |
| Postgres `atlas` | Editable graphs, policies, matches, schemas, catalogue definitions |
| NATS JetStream/KV | Graph runs, requests, work delivery, leases, progress, deduplication |
| Repository objects | Immutable raw HTML and bounded temporary result objects |
| DuckLake with Postgres metadata | Crawls, documents, elements, links, materializations, coverage, graph provenance |

## Worker responsibilities

### `crawl-worker`

The crawl worker owns only acquisition:

1. Claim one frozen `CrawlRequest`.
2. Resolve and enforce its frozen CrawlPolicy.
3. Acquire remote and local capacity.
4. Load the page through a reusable HTTP client or a long-lived Chromium process.
5. Store immutable content-addressed raw HTML through the repository object boundary.
6. Publish a frozen catalogue-ingestion job containing object identity, crawl facts, and graph
   provenance.
7. Release the crawl slot immediately after durable publication.

It must not wait for DuckLake ingestion, materialization, readiness, or edge evaluation.

Each process owns one long-lived browser runtime. Concurrent browser requests use isolated contexts
or pages rather than starting a new Chromium process per URL. Browser lifecycle follows worker
lifecycle.

CrawlPolicy adds one frozen field:

```text
transport: http | browser (default browser)
```

`http` uses one process-wide asynchronous client with connection pooling, decompression, redirect
handling, and the same raw-size bound as browser acquisition. It does not execute JavaScript.
`browser` uses the long-lived Chromium runtime; existing `mode`, `wait`, and run-config overrides
apply only to that transport. The cutover does not infer HTTP safety from a hostname or silently
fall back between transports.

Horizontal scaling signals:

- crawl queue depth;
- age of the oldest queued crawl;
- acquisition p50/p95 latency;
- active HTTP/browser leases;
- CPU and memory; and
- remote-pressure saturation.

The initial cutover deliberately preserves per-worker CrawlPolicy concurrency. Production replica
limits must account for the resulting deployment-wide multiplication. A distributed remote-admission
mechanism is outside this cutover and requires a separate measured design.

### `catalog-worker`

The catalog worker owns all deterministic processing after acquisition:

1. Claim a frozen ingestion or materialization operation.
2. Resolve already-committed deterministic identities before doing work.
3. Read and verify immutable raw HTML.
4. Build the versioned DOM projection.
5. Build navigation-critical system projections, including `page_links`.
6. Compute triggered user materializations.
7. Commit crawl evidence, projections, and coverage through embedded DuckDB/DuckLake.
8. Publish crawl readiness only after the frozen fan-out settles successfully.
9. Evaluate outgoing bounded edge SQL against durable catalogue state.
10. Apply edge deduplication and admit selected URLs as new CrawlRequests.

Horizontal scaling signals:

- ingestion queue depth and oldest age;
- materialization queue depth and oldest age;
- crawl-readiness lag;
- operation compute and commit p50/p95;
- embedded DuckDB CPU and memory;
- Postgres catalogue conflicts/retries; and
- object-store throughput.

Scale catalogue workers independently from crawl workers. The desired ratio is determined by
observed work cost, not by a fixed one-to-one replica count.

## Concurrent DuckLake writers

Catalog workers commit unrelated operations concurrently using the following required protocol.

### Deterministic operation identity

Every mutation has an operation ID derived from immutable inputs using SHA-256 over the exact
UTF-8 strings below:

```text
ingest       "ingest\0" + crawl_request_id + "\0" + CATALOG_RECIPE_VERSION
materialize  "materialize\0" + definition_revision_id + "\0" + scope_kind + "\0" + scope_id
edge         "edge\0" + graph_run_id + "\0" + crawl_request_id + "\0" + edge_id
```

### Operation lease

NATS KV owns a CAS-created, expiring lease:

```text
operation_id
status: reserved | committing | committed | failed
lease_owner
lease_expires_at
attempt
committed_snapshot, nullable
error, nullable
```

The lease prevents overlapping NATS redeliveries from writing the same logical operation. It is
current execution state, not durable history.

The lease avoids duplicate compute but is not the final split-brain write fence. Immediately before
durable identity resolution and commit, the worker acquires a PostgreSQL session advisory lock. The
signed 64-bit lock key is the first eight bytes of the operation SHA-256 interpreted big-endian.
The worker holds the advisory lock across identity resolution and the DuckLake transaction and
releases it afterward; connection death releases it automatically. Unrelated operation IDs do not
block one another. Lock acquisition times out after 60 seconds and NAKs the message with backoff.

### Ambiguous commit recovery

After a timeout or lost response, never blindly repeat a write. Resolve the deterministic crawl,
document, projection, or materialization identity from DuckLake:

```text
durable identity exists and provenance matches
  -> record committed snapshot and acknowledge

identity absent
  -> retry the transaction

identity exists with incompatible provenance
  -> terminal conflict
```

### Conflict retries

Use DuckLake's bounded transaction-conflict retries with metrics for retry count and time. Exhausted
conflicts return to NATS with bounded backoff. They must not become busy loops.

### Transaction boundary

One logical operation commits its data and readiness/coverage facts atomically on the same embedded
DuckDB connection. Never infer success from the globally latest snapshot; record
`last_committed_snapshot()` from the committing connection.

## Table-specific data inlining

Inlining is a primary small-write strategy, not an excuse to place unbounded analytical data in
Postgres. The setting is based on rows per insert, while DOM rows contain variable-width text and
attributes. Threshold selection must therefore consider both row counts and measured bytes.

Initial table configuration is frozen as follows. Later changes are tuning, not contract changes:

| Table class | Candidate policy |
| --- | --- |
| `crawls`, coverage, fan-out tables | `data_inlining_row_limit = 1000` |
| `documents` | `data_inlining_row_limit = 1000`; raw HTML remains in object storage |
| `elements` | `data_inlining_row_limit = 16000`, approximately 2–5 typical projections |
| `materialized.page_links` | `data_inlining_row_limit = 8000` |
| User materialization tables | `data_inlining_row_limit = 10` unless their definition later carries a measured system override |

During the benchmark, validate the frozen elements threshold by collecting:

- rows and encoded bytes per projection at p50/p90/p99;
- Postgres storage growth per 1,000 crawls;
- insert and read latency as inlined rows accumulate;
- CDC discovery latency;
- flush duration and generated Parquet file sizes; and
- query performance before and after flush.

The acceptance target is at least two typical DOM projections per eligible insert. A larger insert
automatically falls through to Parquet; it must not be split merely to force inlining.

Flush policy is table-specific and threshold-driven:

```text
flush a table when any is true:
- estimated inlined bytes exceed 256 MiB
- oldest inlined row exceeds 15 minutes
- explicit maintenance is requested
```

Flush and compaction run under a short catalogue maintenance lease. Ordinary concurrent writes are
paused or allowed to drain only for the affected maintenance window. Snapshot expiration and orphan
deletion remain explicit operator actions.

## Navigation readiness

`page_links` becomes a versioned system projection produced during catalogue ingestion rather than
a second generic materialization scheduled after ingestion:

```text
raw HTML
  -> DOM projection
  -> page_links projection
  -> DuckLake commit
  -> navigation evidence ready
```

This makes trustworthy links scale with ingestion and avoids the current navigation backlog.
Outgoing edges still execute only against durable catalogue evidence.

The physical and logical names remain frozen:

```text
logical system view: views.page_links
physical projection: materialized.page_links
projection version: PAGE_LINKS_PROJECTION_VERSION = 1
```

The projection retains the exact columns and semantics currently defined by `PAGE_LINKS_SQL` in
`backend/control/catalogue_views/system.py`. During base ingestion, the catalog worker inserts
`crawls`, `documents`, and `elements`, then populates `materialized.page_links` for that `crawl_id`
in the same DuckLake transaction. A successful retry resolves the existing `crawl_id` and does not
insert links again.

Remove the `CatalogueMaterialization` row and CDC planning path for the system `page_links`
projection. User-defined materializations remain unchanged. Existing edge SQL continues to query
`materialized.page_links`; no compatibility view or alternate table name is introduced.

The initial cutover preserves the existing single crawl-ready barrier for all materializations in
the crawl's frozen fan-out. Do not add optional dependency membership during this cutover. If a
future slow user materialization demonstrably prevents navigation throughput, revisit the barrier as
a separate product decision with evidence.

The catalog worker performs base ingestion in this exact order under the operation advisory lock:

1. Resolve `crawl_id`; return the existing compatible result if already committed.
2. Verify raw object hash and size for a successful acquisition.
3. Build bounded DOM and page-link Arrow inputs outside the DuckLake transaction.
4. Resolve the materialization definitions active for this crawl and freeze fan-out membership.
5. Begin one DuckLake transaction.
6. Insert the document if its content identity is new.
7. Insert the crawl observation and graph provenance.
8. Insert DOM elements only when this document/recipe projection is new.
9. Insert `materialized.page_links` for this crawl and projection version.
10. Insert the complete frozen fan-out and its membership rows.
11. Commit and record `last_committed_snapshot()` from that connection.
12. Publish deterministic live materialization jobs for planned members.
13. If fan-out is empty, publish outgoing edge jobs immediately.

Publication after commit is recoverable: a catalog reconciler scans planned fan-out members and
republishes their deterministic jobs. Materialization settlement updates member status, coverage,
and aggregate counts in one DuckLake transaction. The final member publishes outgoing edge jobs.
CDC remains for saved-view change publication and historical/backfill discovery; it is not the only
wake-up path for live crawl fan-out.

## Batching

Concurrency and inlining do not remove the benefit of batching. Catalog workers form bounded
microbatches where operations share a recipe and schema revision:

```text
up to N operations
or flush after T milliseconds
or flush before a configured byte ceiling
```

The initial microbatch contract is:

```text
maximum operations = 4
maximum uncompressed prepared bytes = 64 MiB
maximum collection delay = 100 ms
```

Each crawl retains independent identity, coverage, failure reporting, readiness, and graph
provenance even when committed in one DuckLake transaction.

## Backpressure

Horizontal scaling is the first response to catalogue lag. Backpressure is the safety response when
the catalogue cannot keep up despite its allowed capacity.

`atlas_catalog_pressure` is an additional KV bucket with one key, `global`:

```text
status: healthy | elevated | critical
pending_ingest: integer
pending_live_materialization: integer
pending_edge: integer
oldest_work_age_seconds: number
updated_at: timestamp
owner: string
```

One catalog worker holds a 15-second pressure-monitor lease and refreshes this value every five
seconds from JetStream consumer state. Another worker takes over after expiry. Status thresholds are
frozen for the cutover:

```text
healthy:
  total pending < 100 and oldest age < 30 seconds
  crawl workers claim at ATLAS_CRAWL_WORKER_CONCURRENCY

elevated:
  total pending >= 100 or oldest age >= 30 seconds
  crawl workers claim at ceil(configured concurrency / 2)

critical:
  total pending >= 500 or oldest age >= 120 seconds
  crawl workers stop fetching new messages; claimed work finishes
```

A missing or older-than-15-seconds pressure value is treated as `critical`, because catalogue health
cannot be established. Backfill messages do not apply crawl backpressure but remain lower priority.

Backpressure is internal pipeline admission, not a graph budget or CrawlPolicy field. Existing
in-flight work remains visible and recoverable.

## Failure story

The target operator explanation is deliberately short:

| Symptom | Authority and recovery |
| --- | --- |
| URL queued but not loading | NATS crawl request; crawl-worker lease expires and work redelivers |
| Acquisition failed | CrawlRequest becomes terminal with frozen error/provenance |
| Raw HTML exists but crawl is not ready | NATS catalogue operation and DuckLake identity resolution |
| Catalog worker dies during compute | No commit; lease expires; deterministic operation retries |
| Commit response is lost | Resolve deterministic identity in DuckLake before retrying |
| Concurrent transaction conflict | Bounded DuckLake retry, then NATS backoff |
| Materialization fails terminally | Durable failed coverage; source crawl fails readiness visibly |
| Edge query fails | Deterministic edge evaluation fails and source request settles with error |
| Maintenance cannot acquire lease | Maintenance waits; normal writes continue |

There are no Quack connection IDs, remote DuckDB sessions, or separate repository/materialization
worker health chains in the target state.

## Queues and current state

The cutover uses these exact JetStream names:

```text
stream: ATLAS_CRAWL_WORK
subject: atlas.crawl.acquire
durable: atlas-crawl-workers
retention: work queue

stream: ATLAS_CATALOG_WORK
subjects:
- atlas.catalog.ingest
- atlas.catalog.materialize.live
- atlas.catalog.materialize.backfill
- atlas.catalog.edge
durables:
- atlas-catalog-ingest-workers
- atlas-catalog-materialize-live-workers
- atlas-catalog-materialize-backfill-workers
- atlas-catalog-edge-workers
retention: work queue

KV
- atlas_graph_runs
- atlas_crawl_requests
- atlas_graph_progress
- atlas_graph_edge_evaluations
- atlas_crawl_workers
- atlas_catalog_workers
- atlas_catalog_operations
- atlas_catalog_maintenance
- atlas_catalog_pressure
```

Catalog workers poll in this fixed priority order and take at most one message per poll before
checking higher-priority work again:

```text
1. atlas.catalog.ingest
2. atlas.catalog.edge
3. atlas.catalog.materialize.live
4. atlas.catalog.materialize.backfill
```

The streams and buckets supersede `ATLAS_GRAPH_WORK`, `ATLAS_REPOSITORY`,
`ATLAS_MATERIALIZATION_SCOPES`, `ATLAS_MATERIALIZATION_COMMITS`, and their worker-presence state.
There are no dual publishers or consumers after the reset.

## Frozen runtime contracts

### CrawlRequest state

`CrawlRequest` remains the independently claimable URL work unit. Its statuses become:

```text
queued
acquiring
awaiting_catalog
materializing
evaluating_edges
completed
failed
cancelled
```

Required transition ownership:

```text
graph admission: queued
crawl-worker claim: queued -> acquiring
crawl-worker durable catalogue publication: acquiring -> awaiting_catalog
catalog-worker base commit with pending user fan-out: awaiting_catalog -> materializing
catalog-worker base commit with zero user fan-out: awaiting_catalog -> evaluating_edges
catalog-worker final materialization settlement: materializing -> evaluating_edges
catalog-worker edge settlement: evaluating_edges -> completed | failed
cancellation: any non-terminal -> cancelled
```

No worker updates a state owned by the other worker class. Every state update records `updated_at`;
the request additionally stores the first timestamp for every entered stage so performance history
does not depend on transient progress events.

### Crawl acquisition envelope

`atlas.crawl.acquire` contains only the stable lookup key:

```json
{"crawl_request_id": "uuid"}
```

The worker loads the frozen request from `atlas_crawl_requests`. The JetStream message ID is the
request UUID without hyphens.

### CatalogIngestionJob

`atlas.catalog.ingest` contains this frozen Pydantic model with `extra="forbid"`:

```text
operation_id: sha256 hex
recipe_version: integer
graph_id: UUID
graph_run_id: UUID
graph_node_id: UUID
crawl_request_id: UUID
crawl_id: UUID                     # equal to crawl_request_id
requested_url: string
normalized_url: string
final_url: string nullable
status_code: integer nullable
duration_ms: integer nullable
captured_at: timestamp
effective_policy_snapshot_json: object nullable
source_crawl_id: UUID nullable
source_edge_id: UUID nullable
document_id: "sha256:<hex>" nullable
raw_object_key: repository-relative string nullable
raw_size_bytes: integer nullable
warnings_json: array
errors_json: array
published_at: timestamp
```

Successful acquisitions require `document_id`, `raw_object_key`, and `raw_size_bytes`. Failed
acquisitions omit them but still create a durable crawl observation and settle without DOM, links,
or materialization fan-out. `Nats-Msg-Id` is `operation_id`.

### CatalogMaterializationJob

`atlas.catalog.materialize.live` and `.backfill` use:

```text
operation_id: sha256 hex
materialization_id: UUID
definition_revision_id: UUID
scope_kind: document | crawl
scope_id: string
source_snapshot: integer
query_sql: string
result_schema_json: object
published_at: timestamp
```

The job freezes executable SQL and result schema. Later Postgres edits cannot mutate queued work.
`Nats-Msg-Id` is `operation_id`.

### CatalogEdgeJob

`atlas.catalog.edge` uses:

```text
operation_id: sha256 hex
graph_run_id: UUID
crawl_request_id: UUID
crawl_id: UUID
edge_id: UUID
published_at: timestamp
```

The worker loads edge SQL and deduplication mode from the GraphRun's frozen NATS snapshot.
`Nats-Msg-Id` is `operation_id`.

`atlas_graph_edge_evaluations` is keyed by `operation_id` and stores the semantic evaluation state:

```text
operation_id: sha256 hex
graph_run_id: UUID
crawl_request_id: UUID
crawl_id: UUID
edge_id: UUID
status: pending | running | completed | failed
urls_selected: integer
urls_admitted: integer
urls_deduplicated: integer
created_at: timestamp
updated_at: timestamp
error: string nullable
```

The catalog operation lease protects execution; this record drives graph progress and parent
request settlement. After every outgoing frozen edge record is terminal, the catalog worker settles
the source request. A source with no outgoing edges completes immediately after readiness.

### CatalogOperation lease

`atlas_catalog_operations` is keyed by `operation_id` and stores:

```text
operation_id: sha256 hex
kind: ingest | materialize | edge
status: reserved | committing | committed | failed
lease_owner: string
lease_expires_at: timestamp
attempt: integer
committed_snapshot: integer nullable
created_at: timestamp
updated_at: timestamp
error: string nullable
```

Initial lease duration is 120 seconds with a 30-second heartbeat. A worker may steal only an expired
non-terminal lease using KV revision CAS. `committed` and `failed` are terminal until runtime-state
retention deletes the bucket entry. Lease acquisition occurs before expensive preparation.

### Maintenance lease

`atlas_catalog_maintenance` contains one key, `global`, with:

```text
owner: string
operation: flush | compact | schema
lease_expires_at: timestamp
created_at: timestamp
```

The initial lease duration is five minutes with a 30-second heartbeat. Schema migration requires no
active catalog workers and is performed only by `atlas-setup`. Flush and compaction acquire the
lease, stop claiming new catalog work, wait up to 120 seconds for local operations to drain, run one
bounded maintenance operation, and release the lease. A failure releases by TTL.

## Initial configuration

Exact initial environment variables:

```text
ATLAS_CRAWL_WORKER_CONCURRENCY=4
ATLAS_CRAWL_BROWSER_CONTEXTS=4
ATLAS_CRAWL_HTTP_CONCURRENCY=16
ATLAS_CRAWL_HTTP_CONNECT_TIMEOUT_SECONDS=10
ATLAS_CRAWL_HTTP_TOTAL_TIMEOUT_SECONDS=30
ATLAS_CRAWL_BROWSER_PAGE_TIMEOUT_SECONDS=60
ATLAS_CRAWL_WORKER_PRESENCE_TTL_SECONDS=15

ATLAS_CATALOG_WORKER_CONCURRENCY=2
ATLAS_CATALOG_DUCKDB_THREADS=2
ATLAS_CATALOG_DUCKDB_MEMORY_LIMIT=1GB
ATLAS_CATALOG_OPERATION_LEASE_SECONDS=120
ATLAS_CATALOG_OPERATION_HEARTBEAT_SECONDS=30
ATLAS_CATALOG_OPERATION_LOCK_TIMEOUT_SECONDS=60
ATLAS_CATALOG_WORKER_PRESENCE_TTL_SECONDS=15
ATLAS_CATALOG_MAX_DELIVER=5

ATLAS_CATALOG_BATCH_MAX_OPERATIONS=4
ATLAS_CATALOG_BATCH_MAX_BYTES=67108864
ATLAS_CATALOG_BATCH_MAX_WAIT_MS=100

ATLAS_DUCKLAKE_MAX_RETRY_COUNT=10
ATLAS_DUCKLAKE_RETRY_WAIT_MS=100
ATLAS_DUCKLAKE_RETRY_BACKOFF=1.5

ATLAS_CATALOG_INLINE_METADATA_ROWS=1000
ATLAS_CATALOG_INLINE_ELEMENTS_ROWS=16000
ATLAS_CATALOG_INLINE_PAGE_LINKS_ROWS=8000
ATLAS_CATALOG_INLINE_FLUSH_BYTES=268435456
ATLAS_CATALOG_INLINE_FLUSH_AGE_SECONDS=900

ATLAS_CATALOG_PRESSURE_ELEVATED_PENDING=100
ATLAS_CATALOG_PRESSURE_CRITICAL_PENDING=500
ATLAS_CATALOG_PRESSURE_ELEVATED_AGE_SECONDS=30
ATLAS_CATALOG_PRESSURE_CRITICAL_AGE_SECONDS=120
```

All positive integers and byte/duration values are validated at process startup. Unknown or removed
worker environment variables fail deployment validation rather than silently falling back.

## Observability and autoscaling contract

### Crawl fleet

- queued, claimed, and oldest CrawlRequest;
- acquisitions/minute and bytes/minute;
- HTTP versus browser utilization;
- page-load and total handler p50/p95/p99;
- remote-policy wait time; and
- active browser contexts per process.

### Catalogue fleet

- ingestion/materialization/edge queue depth and oldest age;
- operations computed and committed per minute;
- readiness latency from acquisition publication;
- per-table rows and bytes written inline versus Parquet;
- DuckLake transaction retries and conflicts;
- Postgres metadata size and lock time;
- object-store read/write bytes;
- flush and compaction duration; and
- operation lease recovery count.

### Graph runs

Metrics must show time in:

```text
queued for acquisition
acquiring
queued for catalogue
catalogue ingestion/system projection
user materialization
edge evaluation
terminal
```

Persist transition timestamps or bounded stage-duration summaries. Current-state counters alone are
not sufficient for performance attribution.

## Code ownership and work packages

The cutover keeps domain code in its existing packages and introduces only two process entrypoints:

```text
backend/workers/crawl.py    # crawl-worker process
backend/workers/catalog.py  # catalog-worker process
```

Deployment and local commands are frozen:

```text
Compose service: atlas-crawl-worker
Command: python -m workers.crawl
Make target: make crawl-worker

Compose service: atlas-catalog-worker
Command: python -m workers.catalog
Make target: make catalog-worker
```

The API uses its own embedded read-only DuckDB pool and is not a third worker class. It never claims
catalog work or writes DuckLake.

Required ownership after cutover:

| Area | Target modules | Responsibility |
| --- | --- | --- |
| Graph state/admission | `backend/runtime/graph_runs.py`, `graph_progress.py` | GraphRun/CrawlRequest CAS, dedupe, completion |
| Crawl delivery | `backend/runtime/crawl_queue.py` | `ATLAS_CRAWL_WORK` and crawl worker presence |
| Catalog delivery | `backend/runtime/catalog_queue.py` | `ATLAS_CATALOG_WORK`, envelopes, operation/maintenance/pressure KV |
| Acquisition | `backend/actions/crawl/` | HTTP/browser transport and immutable acquisition result |
| Raw objects | `backend/repository/objects/` | Content-addressed HTML |
| Catalogue transactions | `backend/repository/catalogue/` | Embedded DuckLake attach, identity resolution, commit, maintenance |
| Base preparation | `backend/repository/ingestion/` | DOM and system projection preparation |
| DOM | `backend/dom/` | Versioned DOM recipe |
| Materialization | `backend/materialization/` | Frozen-scope computation and coverage transaction |
| Worker entrypoints | `backend/workers/` | Claim loops, lifecycle, presence, priority, graceful drain |
| API/CLI | `backend/api/`, `backend/cli/` | Validation and administration only |

Delete these process entrypoints after their callers move; do not leave wrappers:

```text
backend/runtime/worker.py
backend/repository/worker.py
backend/materialization/worker.py
backend/repository/quack_server.py
```

The work is divided into low-overlap packages and merged in this dependency order:

1. **Contracts:** config, Pydantic envelopes, NATS streams/KV, statuses, transition timestamps.
2. **Crawl worker:** reusable transports, acquisition-only handler, raw object write, ingestion
   publication.
3. **Embedded catalogue:** local DuckDB/DuckLake configuration, advisory operation lock, concurrent
   transaction retry, identity resolution.
4. **Base ingestion:** DOM, `page_links`, fan-out planning, table inlining, base transaction.
5. **Materialization and edges:** live/backfill execution, coverage, readiness, edge SQL, admission.
6. **Operations:** maintenance lease, flush/compaction, pressure monitor, autoscaling metrics.
7. **Deletion/deployment:** two services, removed Quack and old streams, docs/UI/CLI updates.

Packages 2 and 3 can proceed in parallel after package 1. Packages 4 and 5 depend on package 3.
Package 6 depends on the final streams from package 1 and transaction boundary from package 3.

## Required test matrix

### Unit and contract tests

- Every envelope rejects extra fields and round-trips exact JSON.
- Every operation ID is stable across processes and changes when its recipe/revision changes.
- Every CrawlRequest transition accepts only its owning worker and legal prior state.
- Pressure thresholds produce the frozen healthy/elevated/critical actions.
- Table options are provisioned idempotently with the exact initial values.

### Worker lifecycle tests

- Crawl worker starts one browser runtime and reuses it across at least 100 requests.
- Each browser request receives an isolated context/page and cleanup runs after success, failure, and
  cancellation.
- Crawl handler acknowledges only after raw object and catalog publication are durable.
- Catalog worker gracefully drains claimed operations before shutdown timeout.
- Presence TTL expires after an ungraceful worker death.

### Concurrent writer tests

Run with 1, 2, 4, and 8 catalog workers against a Postgres DuckLake catalogue and shared S3-compatible
object storage:

- distinct crawl ingestions commit concurrently;
- two deliveries of one operation produce one logical result;
- lease expiry during compute does not produce duplicate commit because of the advisory lock;
- process death before transaction, during transaction, after commit, and before ACK recovers;
- lost commit response resolves from durable identity;
- transaction conflicts retry within the configured bound;
- crawl, document, elements, links, coverage, and fan-out provenance agree;
- CDC observes every snapshot once and preserves schema-boundary ordering.

### Inlining and maintenance tests

- Inserts at/below each table threshold create inlined data; inserts above it create Parquet.
- A four-crawl microbatch preserves independent crawl and document identities.
- Flush by table preserves current reads and time travel.
- Flush/compaction cannot overlap schema maintenance.
- Worker death while holding the maintenance lease recovers after TTL.
- Postgres growth and query latency are captured for 1,000 representative crawls.

### End-to-end graph tests

- One-node graph settles one URL.
- Self-edge with graph dedupe reaches quiescence.
- Crawl/document dedupe scopes retain their current semantics.
- `materialized.page_links` is queryable immediately after base ingestion commit.
- Edge execution never precedes successful frozen fan-out settlement.
- A terminal materialization failure visibly fails the source request.
- Cancellation settles current state without new admissions.
- Both worker classes restart independently during an active 1,000-URL run.

## Development reset and deployment sequence

This is a destructive greenfield cutover. The first deployment uses this exact sequence:

1. Stop API, old runtime/repository/materialization workers, and Quack.
2. Build the new backend image containing both worker entrypoints.
3. Run `atlas-setup` to provision DuckLake tables/options and Postgres control schema.
4. Purge/delete the superseded graph/repository/materialization work streams and runtime KV.
5. Recreate `ATLAS_CRAWL_WORK`, `ATLAS_CATALOG_WORK`, and the frozen KV buckets.
6. Start one catalog worker and wait for catalogue validation and healthy presence.
7. Start one crawl worker and wait for healthy presence.
8. Start the API with an embedded read-only DuckDB catalogue pool.
9. Run the one-URL smoke graph, self-edge quiescence graph, and concurrent-writer smoke.
10. Scale catalog workers to two only after the smoke suite passes.

Disposable DuckLake development state is recreated because `page_links` changes from generic
materialization ownership to a system ingestion projection. Immutable raw objects may be retained,
but no compatibility replay is automatic. Production migration of retained DuckLake history is a
separate explicitly authorized project.

## Cutover phases

The benchmark implementation lives at `backend/benchmarks/two_worker_pipeline.py` and writes a JSON
summary under untracked `.atlas/benchmarks/`. It supports:

```text
--catalog-workers 1|2|4|8
--fixture wikipedia-one-hop
--fixture retained-1000
--crash-point none|before_commit|during_commit|after_commit_before_ack
--json-summary
```

`wikipedia-one-hop` uses the frozen graph shape from run
`a5d192cf-a46f-44aa-b5a2-aa618c7c439a`: one Wikipedia seed and separate internal/external target
nodes. `retained-1000` replays a manifest of immutable raw-object identities without network access
to isolate catalogue throughput. The manifest records document sizes and expected DOM/link counts;
it contains no copied raw HTML.

Observed pre-cutover reference from 2026-07-12, used only as a regression baseline:

```text
healthy acquisition: 20-34 crawls/minute
materialization drain: approximately 11 scopes/minute
Wikipedia acquisition average: 4.26 seconds
external acquisition average: 5.40 seconds
elements per document: average 3,652; p90 11,765; max 30,997
```

### Phase 0: benchmark frozen contracts

- Capture the current Wikipedia-like workload as a repeatable benchmark fixture.
- Record acquisition, ingestion, materialization, readiness, Postgres, Quack, and file-layout
  baselines.
- Measure per-table rows and bytes per operation.
- Assert the frozen operation identities, envelopes, queue names, and initial settings in contract
  tests.
- Add crash points for pre-commit, post-commit/pre-ack, and lost-response tests.

Exit: baseline is reproducible and every target change has a measurable target.

### Phase 1: build the crawl worker

- Extract acquisition from the current runtime worker.
- Reuse one Chromium runtime per process with isolated contexts/pages.
- Add a reusable HTTP transport selected by CrawlPolicy.
- Publish frozen catalogue-ingestion jobs without waiting for catalogue completion.
- Keep graph request/progress updates in NATS.

Exit: crawl workers can restart independently and sustain acquisition without holding slots on
catalogue work.

### Phase 2: embedded catalog worker, one replica

- Replace Quack catalogue access with embedded DuckDB/DuckLake.
- Merge repository ingestion and materialization execution into one catalog-worker binary.
- Implement operation leases, advisory operation locks, identity resolution, and bounded conflict
  retries before accepting writes.
- Produce DOM and `page_links` in the ingestion path.
- Preserve deterministic identity resolution and current readiness semantics.
- Configure and measure table-specific inlining.

Exit: one catalog worker passes all correctness tests and outperforms the current Quack path.

### Phase 3: scale concurrent catalog workers

- Run 1, 2, 4, and 8 replicas against the benchmark.
- Prove concurrent redelivery, ambiguous commit, and worker-death recovery.
- Add bounded microbatching.
- Add the maintenance lease and table-specific flush/compaction.

Exit: at least two replicas commit concurrently with no duplicate logical identities, complete CDC,
and useful throughput scaling.

### Phase 4: autoscaling and backpressure

- Export oldest-age and saturation metrics.
- Scale crawl and catalogue deployments independently.
- Add catalogue-lag backpressure to crawl claiming.
- Load-test remote CrawlPolicy pressure under replica changes.

Exit: sustained acquisition cannot create an unbounded catalogue-readiness backlog.

### Phase 5: delete the superseded architecture

- Remove Quack from the production correctness path and deployment.
- Remove repository-worker and materialization-worker services and their dedicated queues.
- Remove remote Quack connection/recovery code.
- Rename runtime-worker responsibilities to the final crawl/catalog worker boundaries.
- Update `ARCHITECTURE.md`, `CRAWL_GRAPHS.md`, `HAZARDS.md`, deployment docs, environment variables,
  metrics, and diagrams.
- Delete obsolete compatibility-free code and reset disposable development runtime state.

Exit: Atlas has exactly two worker classes and one documented execution path.

## Acceptance criteria

### Simplicity

- An operator can locate a stalled URL as either crawl-plane or catalogue-plane work from one page.
- No worker waits synchronously on another worker's private session or connection.
- Quack connection identity is absent from runtime and failure contracts.
- Architecture documentation describes only two worker classes.

### Correctness

- No duplicate crawl, document, projection, or materialization identity under concurrent delivery.
- Lost commit responses resolve safely without blind writes.
- Outgoing edges never see incomplete navigation evidence.
- CDC observes every concurrent commit with correct schema-boundary ordering.
- Worker termination at every injected crash point eventually settles or visibly fails the operation.

### Scalability

- Catalogue throughput remains at least 1.25 times sustained acquisition throughput at the intended
  production crawl-worker count.
- Two catalog workers achieve at least 1.6 times one-worker catalogue throughput on
  `retained-1000`; four achieve at least 2.5 times unless object-store or Postgres saturation is
  identified with evidence.
- DuckLake transaction conflicts remain below 5% of attempted operation commits and p95 conflict
  retry delay remains below two seconds.
- Crawl workers spend no runtime slot time waiting for catalogue readiness.
- At sustained benchmark load, p95 acquisition-publication-to-navigation-ready latency is below 30
  seconds and catalogue oldest-work age does not grow for three consecutive five-minute windows.

### Storage

- Table-specific inlining absorbs at least two typical element projections per eligible insert.
- After 1,000 representative crawls, Postgres catalogue growth is reported and remains below the
  uncompressed prepared Arrow bytes; p95 point-read latency is no more than 1.25 times the flushed
  baseline; CDC publication p95 is below five seconds.
- Flush creates bounded, useful Parquet files and preserves time travel.
- Compaction and cleanup never race without the maintenance lease.

## Decision gates

Stop and reassess rather than layering workarounds if any of these fail:

1. Postgres-backed DuckLake concurrent commits do not scale beyond one worker under Atlas's actual
   schemas and object store.
2. Operation-level idempotency cannot reliably resolve ambiguous commits.
3. Elements inlining at two-projection scale causes unacceptable Postgres growth or query latency.
4. Embedded DuckDB memory per replica makes horizontal catalogue scaling uneconomical.
5. CDC cannot preserve complete, ordered publication across concurrent writers.

The fallback for a failed decision gate is a measured narrow constraint—such as bounded commit
concurrency for one table—not a return to a global remote single-writer architecture by default.
