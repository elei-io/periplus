# Worker architecture

This document is the deployment and scaling contract for Atlas. It replaces the former combined
crawl/catalogue worker design; new work must preserve these boundaries and must not add
compatibility paths for the superseded topology.

## Goals

Atlas scales four kinds of work independently:

1. acquire pages;
2. ingest retained evidence and continue crawl graphs;
3. maintain live materialized views; and
4. maintain DuckLake storage.

The critical product invariant is:

```text
acquisition -> base ingestion -> navigation ready -> outgoing edges

                                      independent of

                         live materialization settlement
```

A stopped, slow, or failed materialization deployment may make views stale. It must not hold a
browser, delay base ingestion, prevent navigation readiness, or keep a graph run active.

## Deployment topology

```text
                         NATS crawl work, routed by frozen transport
                                      |
             +------------------------+------------------------+
             |                        |                        |
      HTTP acquisition        browser acquisition      provider acquisition
          workers                  workers                  workers
             |                        |                        |
             +------------ immutable raw HTML ----------------+
                                      |
                              frozen ingestion job
                                      |
                              ingestion workers
                         embedded DuckDB / DuckLake
                         DOM + system projections
                         navigation package + edges
                                      |
                        asynchronous scope notifications
                                      |
                         materialization workers
                         embedded DuckDB / DuckLake
                         CDC + backfill + live commits

maintenance worker -- global maintenance lease --> bounded compaction / cleanup
```

Every deployment is independently restartable and observable. HTTP, browser, and external-provider
acquisition share one output contract, so downstream ingestion never branches on how HTML was
obtained.

## DuckDB and DuckLake process model

DuckDB is embedded in an Atlas worker process; it is not a central Atlas database server. The
simplest safe worker model is:

- one worker process per pod;
- one owned DuckDB/DuckLake connection per execution lane;
- one active catalogue operation per process initially; and
- horizontal concurrency from additional worker replicas.

Do not share one DuckDB connection between independently scheduled coroutines. A query must own its
connection until its result has been fully consumed or closed.

DuckLake uses PostgreSQL for shared metadata and object storage for Parquet, so unrelated operations
from multiple worker processes may commit concurrently. Horizontal safety still requires
deterministic operation identity, an expiring NATS operation lease, a PostgreSQL advisory lock
around identity resolution and commit, bounded transaction-conflict retries, and ambiguous-commit
reconciliation. Replicas do not make overlapping writes conflict-free.

## Acquisition workers

Acquisition workers own only remote page acquisition:

1. claim a frozen crawl request from the subject for its transport;
2. acquire the deployment-wide CrawlPolicy permit;
3. load one page;
4. release remote and local transport capacity;
5. store immutable content-addressed raw HTML; and
6. publish a frozen ingestion job.

They do not open DuckLake, parse the retained DOM, evaluate graph edges, wait for ingestion, or run
materializations.

### HTTP acquisition

HTTP workers use one process-wide asynchronous client with connection pooling. They are lightweight
and may use bounded in-process I/O concurrency. Scale them from HTTP queue depth and oldest-message
age.

### Browser acquisition

Browser workers own one long-lived Chromium runtime per process. Each request uses an isolated page
or context. Start with one active page per pod; raise local concurrency only after measuring memory
and CPU isolation. Scale primarily by adding replicas.

Chromium belongs only in the browser-worker image. HTTP, ingestion, materialization, API, and
maintenance images must not install it merely for package uniformity.

### External-provider acquisition

An external provider remains one page-acquisition transport. It receives its own queue when its
capacity, batching, or rate limits differ from HTTP and browser work. It still returns the same raw
HTML and provenance contract and never performs graph traversal itself.

### Transport routing

The frozen CrawlPolicy selects the work subject before publication. Target subjects are distinct,
for example:

```text
atlas.graph.crawl.http
atlas.graph.crawl.browser
atlas.graph.crawl.provider.<name>
```

Do not use one shared consumer and inspect message bodies after claiming: that prevents Kubernetes
from scaling transport fleets independently.

## Ingestion workers

Ingestion workers own deterministic processing required to make one captured page durable and allow
its graph to continue:

1. claim a frozen ingestion operation;
2. resolve an already-committed deterministic identity;
3. read and verify immutable raw HTML;
4. build the versioned DOM projection;
5. build navigation-critical system projections such as `page_links`;
6. commit crawl, document, element, projection, and provenance evidence;
7. write and verify the bounded Arrow navigation package;
8. publish navigation readiness;
9. evaluate outgoing bounded edge SQL; and
10. admit returned URLs as new transport-routed CrawlRequests.

An ingestion worker may publish asynchronous user-materialization scope work after its base commit,
but it never evaluates or commits that work and never waits for it.

`page_links` is a system projection even if it is exposed through `views.page_links`. It is required
for navigation and is committed with base ingestion. User-created materialized views are not system
projections and remain off the graph critical path.

Scale ingestion workers from ingestion queue depth, oldest age, navigation-readiness latency,
operation latency, DuckLake conflict rate, CPU, memory, and object-store throughput.

## Materialization workers

Materialization workers own the complete lifecycle of live user materializations:

1. discover document- or crawl-scoped changes from durable DuckLake CDC positions;
2. enumerate bounded historical backfill scopes after activation;
3. publish or claim an idempotent scope operation;
4. validate that the view definition and incremental discriminator are still active;
5. evaluate one bounded scope;
6. stage deterministic Arrow when retry recovery benefits from it;
7. atomically replace that scope and record coverage in DuckLake;
8. advance durable CDC or backfill progress; and
9. settle per-view and per-graph materialization lag.

Materialization is always live. Atlas has no manually refreshed materialization mode. If a view
cannot be maintained from a supported scope, activation is disabled with the exact reason.

Materialization workers do not acquire pages, ingest base crawl evidence, publish navigation
readiness, evaluate graph edges, or participate in graph-run completion. Their failure affects view
freshness only.

Start with one active scope operation per worker process. Scale from pending scope count, oldest
scope age, per-view lag, compute and commit latency, failure rate, memory, and transaction conflicts.
One hot view may remain limited by overlapping scope or metadata writes; adding replicas is not a
substitute for bounded scopes and correct partitioning.

## Maintenance worker

The maintenance worker owns bounded off-path storage upkeep:

- flush explicitly eligible inlined data;
- compact DuckLake files;
- apply configured snapshot and scheduled-file retention;
- remove abandoned staging objects after their grace period; and
- run narrowly scoped catalogue checks and repair.

It runs as one replica by default and uses an expiring global maintenance lease. Before mutating
storage it waits for active ingestion and materialization commits to drain. Loss of the lease stops
the operation. Maintenance never consumes acquisition, ingestion, materialization, or graph-edge
work.

## Queue and state boundaries

NATS JetStream/KV owns current execution, work delivery, leases, deduplication, worker presence,
queue progress, and ingestion/materialization cursors where applicable. DuckLake remains the
authority for durable crawl evidence, materialized rows, and coverage. PostgreSQL `atlas` remains
the control plane for editable definitions.

`atlas_catalog_operations` is a file-backed, self-expiring KV bucket. Phase-qualified keys lease
ingestion commits, materialization compute and commits, and the singleton materialization scheduler
loops. The lease suppresses overlapping redelivery work; it is not durable completion state.
PostgreSQL advisory locks remain the final commit fence, and DuckLake identity/coverage remains the
authority after an ambiguous outcome.

At-least-once delivery is expected at every boundary. A worker acknowledges only after the next
durable state exists:

```text
acquisition ACK     after raw HTML and ingestion publication are durable
ingestion ACK       after base evidence and recoverable navigation readiness are durable
materialization ACK after scope replacement and coverage are durable
edge ACK            after every admitted target request is durable or terminal
```

## Health and autoscaling

Process liveness is insufficient. Every deployment reports whether its owned queue is making
progress.

| Deployment | Primary scaling signal | Health failure examples |
| --- | --- | --- |
| HTTP acquisition | oldest HTTP crawl age | queue stops advancing, client unavailable |
| Browser acquisition | oldest browser crawl age | Chromium unavailable, queue stops advancing |
| Provider acquisition | oldest provider crawl age | provider auth/rate failure |
| Ingestion | oldest ingestion age | DuckLake unavailable, navigation publication stalled |
| Materialization | oldest pending scope age | CDC stalled, scope commits not advancing |
| Maintenance | scheduled upkeep age | lease loss, bounded operation repeatedly fails |

Queue age is more important than raw queue depth because it directly represents user-visible lag.
CPU and memory are safety and capacity signals, not correctness state.

## Kubernetes shape

The default production deployment has:

```text
atlas-api
atlas-crawl-http-worker                 replicas: autoscaled
atlas-crawl-browser-worker              replicas: autoscaled
atlas-crawl-provider-<name>-worker      replicas: autoscaled when configured
atlas-ingestion-worker                  replicas: autoscaled
atlas-materialization-worker            replicas: autoscaled
atlas-maintenance-worker                replicas: 1
```

Acquisition policy concurrency remains deployment-wide and is enforced through NATS leases, so
adding pods cannot exceed a remote's configured pressure ceiling. Per-pod browser concurrency only
protects that Chromium process.

## Direct greenfield cutover

Do not run old and new worker contracts indefinitely. The migration is:

1. Split materialization planning, compute, commit, dematerialization, and recovery out of the
   combined catalogue process.
2. Run one ingestion replica and one materialization replica and prove that either deployment can
   be stopped without stopping the other.
3. Add operation fencing and failure-injection tests, then prove safe multi-replica ingestion and
   materialization.
4. Split crawl work by frozen transport and deploy separate HTTP and browser images.
5. Reset disposable NATS state when subjects or durable consumers change.
6. Reset disposable Postgres/DuckLake state when the greenfield baseline schema changes.
7. Delete the combined catalog worker, its cross-responsibility connection lock, old subjects, old
   durable consumers, and obsolete configuration in the same cutover. Each remaining DuckDB-owning
   process still serializes its own catalogue operations through one local execution lane.

There are no dual reads, dual writes, fallback subjects, aliases, or legacy worker modes.

The cutover is implemented. The graph stream now uses only the transport-specific subjects and
durable consumers above, acquisition processes do not open DuckLake, and Chromium is installed only
in the browser image. Disposable NATS state must be reset when this contract is first deployed; a
greenfield Postgres/DuckLake schema change likewise requires resetting disposable development state.

## Required proof before production

- With materialization stopped, crawls ingest and graph runs complete while per-view lag rises.
- Restarting materialization drains durable backlog without reacquisition.
- With acquisition stopped, retained materialization backlog can continue draining.
- Killing any worker before compute, during staging, and around commit produces eventual exactly-once
  durable effects under at-least-once delivery.
- Multiple ingestion replicas commit distinct crawls concurrently.
- Multiple materialization replicas commit distinct scopes concurrently.
- Conflicting writes retry with bounded backoff and never busy-loop.
- Browser replicas can be added or removed without changing HTTP capacity.
- Maintenance cannot overlap an unfenced hot-path commit.
- Metrics attribute queue age and failures to the responsible deployment and, for materialization,
  to the affected view and graph run.
