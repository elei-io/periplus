# Architecture

Atlas has one acquisition path and one graph-driven way to compose subsequent acquisition:

```text
API
        |
        +-- graph trigger --> GraphRun in JetStream/KV
                                 |
                           CrawlRequest
                                 |
                          crawl worker
                                 |
                               crawl
                                 |
              +------------------+------------------+
              |                                     |
    immutable HTML.zst                    frozen ingestion job
    repository object store                    JetStream
                                                     |
                                             catalog worker
                                                     |
                              +----------------------+------------------+
                              |                                         |
                          DuckLake                         Arrow navigation package
                              |                           repository object store
                    asynchronous analytics                           |
                    and materializations                    NATS verified reference
                                                                        |
                                                                 crawl ready
                                                     |
                                       outgoing bounded SQL edges
                                                     |
                                 admitted target-node CrawlRequests

Off the graph hot path:

maintenance worker -- global NATS lease --> bounded compaction / cleanup --> DuckLake
```

`crawl` is the only page-acquisition primitive. A graph node maps admitted URL inputs to crawl
work. After a crawl is durable, the catalog worker projects a bounded Arrow navigation package from
the immutable HTML, verifies it in the repository object store, and durably publishes its reference
through NATS. Every outgoing edge evaluates bounded SQL over `nav.links` for that `crawl_id`;
returned URLs are offered to the target nodes. A self-edge expresses
bounded recursion such as pagination or a site walk.

The graph model is deliberately crawl-specific. Nodes do not execute arbitrary compute, and edges
do not perform external side effects. Human-readable result visualization is outside graph
execution; users inspect retained findings with catalogue SQL, saved queries, views, and
materializations. The detailed contract is in [CRAWL_GRAPHS.md](CRAWL_GRAPHS.md).

Catalogue materializations add one supervised planner/evaluator beside ingestion. A managed
DuckLake view may have at most one attached materialization; materialization is a live capability
of that view, not a separate catalogue product or a source of duplicate outputs. Saved queries
cannot be materialized. Activation declares whether updates are keyed by document or crawl and
which view output column is that incremental discriminator. DuckLake CDC
discovers changed document or crawl scopes and historical activation scans page through bounded
scope IDs. Both publish JetStream work. Evaluation streams a bounded Arrow object through the
configured repository object store. The catalog worker verifies and commits it through its embedded
DuckDB/DuckLake connection. Commit delivery is supervised: a failed consumer makes the worker
unhealthy and terminates its main loop, while post-commit fan-out settlement failures NAK only the
affected message for idempotent redelivery. Startup reconciliation keyset-pages every crawl missing
a frozen fan-out exactly once per planner lifecycle rather than repeatedly publishing the first
page.

JetStream consumer depths are deployment-wide pipeline diagnostics because materialization stages
share consumers. User-facing lag comes from authoritative DuckLake state instead: each view reports
planned fan-out members for its current definition plus remaining activation backfill scopes, and
Graph Metrics attributes live pending and failed fan-out members through `crawls.graph_run_id`,
so each run carries its own accumulated materialization lag.

## State ownership

| Owner | Authoritative state | Must not own |
| --- | --- | --- |
| Postgres `atlas` | Editable crawl graphs, graph nodes and edges, crawl policies, URL matches, and catalogue definitions | Graph execution, queued crawl requests, crawl history, HTML, DOM elements |
| NATS JetStream/KV | Graph runs, crawl requests, admission and deduplication state, work delivery, workers, progress, ingestion state, navigation-package integrity/lifecycle references, and edge-evaluation state | Irreplaceable long-term analytics or navigation package bytes |
| Repository objects | Immutable content-addressed raw HTML, ephemeral run-scoped Arrow navigation packages, and bounded temporary staging objects | Mutable execution metadata |
| DuckLake | Documents, crawl attempts, graph provenance, versioned DOM elements, materialized derived facts, and durable materialization coverage | Graph topology or current execution state |
| Prometheus | Operational counters, gauges, and histograms | Correctness-critical state |

Postgres may retain stable definition metadata, but that does not make a graph run a Postgres
entity. DuckLake may retain graph, node, run, request, edge, and policy provenance for analysis, but
it does not own their editable definitions or current execution.

## Crawl graph execution

A graph trigger creates current `GraphRun` state in NATS and offers seed URLs to entry nodes. Each
admitted URL becomes independently claimable crawl work. Once a crawl passes the readiness fence,
every outgoing edge runs bounded SQL for that crawl and offers returned URLs to the ordinary
crawl-request path:

```text
crawl ingested + verified navigation package referenced by NATS
        -> outgoing SQL edges
        -> URL-matched CrawlPolicy resolution
        -> CrawlRequests
```

Crawl policy controls pressure and acquisition behavior for matching remotes; it does not bound
graph work. Edge SQL owns candidate selection and intended termination. Deployment-level hard
ceilings stop runaway runs. NATS owns current claims, deduplication, readiness processing, and
idempotent at-least-once delivery. Raw HTML remains the recovery authority; missing navigation
packages are regenerated from it. DuckLake enrichment is asynchronous and is not a graph readiness
barrier. The complete
entity, messaging, recursion, and failure contract lives in [CRAWL_GRAPHS.md](CRAWL_GRAPHS.md).

## Crawl and ingestion

For a captured page, the crawl worker:

1. Normalizes the request and acquires the page under its deployment-wide CrawlPolicy permit and,
   for browser work, a bounded worker-local browser permit.
2. Releases remote-acquisition capacity after the remote response completes.
3. Hashes the raw UTF-8 HTML and stores compressed content idempotently under
   `raw/html/sha256/<prefix>/<hash>.html.zst`.
4. Publishes a frozen ingestion job containing repository-relative identity and graph provenance.

Catalog workers verify raw objects, build bounded page-local DOM staging, commit
document/crawl/element microbatches, publish verified run-scoped Arrow navigation packages, execute
scoped materializations asynchronously, and evaluate outgoing edges from `nav.*` packages after
durable readiness. Each process uses embedded DuckDB against the shared Postgres-backed
DuckLake catalogue. Concurrent writers use deterministic operation identity and bounded conflict
retry; redelivery resolves durable identity before repeating a write.

Repository microbatches accumulate across JetStream deliveries and flush at the first item,
element-row, staged-byte, or five-second oldest-item limit. Compatible materialization scopes are
likewise grouped by target and definition revision, then appended through one Parquet file and one
DuckLake transaction; partial groups also flush after five seconds. Timers bound low-volume latency
while row and byte limits govern high-volume file quality. Crawl rows are partitioned by capture
date; DOM elements are hash-bucketed by document identity; and frozen fan-out tables are
hash-bucketed by crawl identity. Setup rewrites pre-partition files once so the new layout benefits
retained evidence as well as new crawls. Crawl-scoped link materialization and fan-out settlement
can therefore prune most unrelated object-store files. The managed `page_links` definition derives
anchor text set-wise instead of invoking a descendant scan once per link.

Terminal ingestion failures enter a size- and age-bounded file-backed dead-letter stream. Explicit
repository commands inspect and requeue them. The maintenance worker runs bounded,
threshold-driven DuckLake small-file compaction independently of ingestion. It schedules this work
locally and uses one expiring NATS KV lease solely to exclude concurrent catalogue writers and
maintenance replicas; it does not publish work to itself or retain worker/operation bookkeeping.
Metadata inlining is disabled after reproducible
Postgres-backed inline-reader crashes; maintenance still flushes previously inlined rows before
compaction. Compaction output is bounded by DuckLake's
`max_compacted_files`; superseded files are reclaimed only after the configured read-safety grace
period. Snapshot expiration, orphan deletion, and other retention-changing maintenance remain
explicit operations, never hidden side effects of reads or crawls.

Repository and materialization transactions annotate snapshots with an Atlas author, operation
description, and bounded identifiers. Write responses use `last_committed_snapshot()` for the
current logical connection rather than the globally latest snapshot, so concurrent writers cannot
misattribute a commit.

## Reads and cache

Acquisition reads through the repository boundary. A cache hit requires matching normalized URL and
input hash, acceptable age and quality, a verified raw object, and a current DOM parser recipe.
Missing evidence is a miss. A stale structural projection can be rebuilt from raw HTML through the
same ingestion path.

Public point reads use document content IDs and crawl UUIDs. Local paths and DuckLake physical
Parquet paths are implementation details.

## Bounds

HTML size, DOM element count, staging bytes, ingestion batch size, NATS envelopes, remote pressure,
edge SQL results, materialization scopes, browser concurrency, graph-run current state, and platform
runaway ceilings have explicit limits. Limit failures must be clear and terminal; durable state must
not be silently truncated.

CrawlPolicy concurrency is a deployment-wide remote-pressure ceiling enforced by expiring NATS KV
leases for the frozen policy revision. Browser concurrency is additionally bounded per worker to
protect local CPU and memory; replica count determines the deployment's physical browser capacity.
The policy lease is not a global browser semaphore: HTTP, browser, and external-provider work all
use it, and only actual remote acquisition holds a lease.

## Code ownership

- `backend/control/` owns editable Postgres-backed crawl graphs, policies, matches, and
  catalogue definitions.
- `backend/runtime/` owns NATS-backed graph runs, crawl requests, admission, deduplication, progress,
  worker delivery/current state, the global maintenance lease, and crawl capacity.
- `backend/workers/` owns the crawl, catalog, and maintenance process lifecycles.
- `backend/actions/` contains the current acquisition and analysis implementation during the graph
  cutover; it must not gain new navigation primitives or traversal loops.
- `backend/repository/objects/` owns immutable content-addressed raw HTML.
- `backend/repository/ingestion/` owns repository queueing, validation, pipeline, health, and
  recovery.
- `backend/materialization/` owns scoped planning, CDC discovery, bounded evaluation, durable
  failure administration, coverage, and the repository-writer commit contract.
- `backend/repository/catalogue/` implements DuckLake behind the repository boundary.
- `backend/repository/service.py` is the application-facing durable repository boundary.
- `backend/dom/` owns the versioned structural DOM projection.
- `backend/api/` and `backend/cli/` are thin adapters.
- `backend/db/` owns generic SQLAlchemy setup and Alembic migrations.

## Exact contracts

Architecture documents ownership and invariants. Code remains authoritative for currently
implemented shapes while the graph cutover is in progress:

- Crawl graph target contract: [`docs/CRAWL_GRAPHS.md`](CRAWL_GRAPHS.md)
- Worker cutover contract: [`docs/TWO_WORKER_ARCHITECTURE_PLAN.md`](TWO_WORKER_ARCHITECTURE_PLAN.md)
- Configuration and defaults: [`.env.example`](../.env.example)
- Postgres models: [`backend/control/`](../backend/control/)
- Runtime state and queues: [`backend/runtime/`](../backend/runtime/)
- Repository messages: [`backend/repository/ingestion/queue.py`](../backend/repository/ingestion/queue.py)
- DuckLake tables: [`backend/repository/catalogue/schema.py`](../backend/repository/catalogue/schema.py)
- DOM projection: [`backend/dom/`](../backend/dom/)
- HTTP surface: the FastAPI-generated OpenAPI document
