# Architecture

Atlas has one page-acquisition contract, one durable ingestion path, and one graph-driven way to
compose subsequent acquisition. Acquisition, ingestion, user materialization, and storage
maintenance are separate scaling and failure domains.

```text
API
 |
 +-- graph trigger --> GraphRun in JetStream/KV
                          |
                    CrawlRequest
                          |
             route by frozen transport
                /         |          \
            HTTP       browser     provider
             workers     workers     workers
                \         |          /
                  immutable HTML.zst
                          |
                  frozen ingestion job
                          |
                  ingestion worker
                          |
       +------------------+-------------------+
       |                  |                   |
    DuckLake       Arrow navigation      outgoing bounded
  base evidence        package              SQL edges
       |                  |                   |
       |             verified reference       +--> CrawlRequests
       |
       +--> asynchronous scope work --> materialization workers
                                            |
                                    live materialized views

Off the graph hot path:

maintenance worker -- global lease --> bounded compaction / cleanup --> DuckLake
```

The detailed deployment contract is [WORKER_ARCHITECTURE.md](WORKER_ARCHITECTURE.md).
Crawl-policy sampling and paired evidence are defined in [TRIALS.md](TRIALS.md).

## Critical-path invariant

The graph critical path ends at base ingestion, navigation readiness, and outgoing edge admission:

```text
acquire -> retain raw HTML -> ingest base evidence -> navigation ready -> evaluate edges
```

User materialization is asynchronous:

```text
base evidence or CDC change -> materialization scope -> live view catches up
```

A slow or unavailable materialization deployment may make a view stale. It must not hold remote or
browser capacity, delay base ingestion, block outgoing edges, or keep a graph run active.

Navigation-critical system projections such as `page_links` are produced by ingestion and are not
treated as user materializations, even when exposed through the `views` namespace.

## State ownership

| Owner | Authoritative state | Must not own |
| --- | --- | --- |
| PostgreSQL `atlas` | Editable graphs, nodes, edges, policies, matches, schemas, queries, views, and materialization definitions | Current graph execution, crawl history, HTML, DOM evidence |
| NATS JetStream/KV | Graph runs, crawl requests, transport-routed delivery, admission, deduplication, operation leases, worker presence, progress, ingestion delivery, and materialization delivery | Irreplaceable analytical history or large payload bytes |
| Repository objects | Immutable content-addressed raw HTML, bounded Arrow navigation packages, and deterministic temporary staging objects | Mutable execution metadata |
| DuckLake | Documents, crawl attempts, graph provenance, versioned DOM elements, system projections, live materialized rows, snapshots, and durable coverage | Editable graph topology or current execution state |
| Prometheus | Operational counters, gauges, and histograms | Correctness-critical state |

PostgreSQL may retain stable definition metadata, but graph runs remain NATS entities. DuckLake may
retain graph and request provenance for analysis, but it does not own their editable definitions or
current execution.

## Worker ownership

### Acquisition workers

Acquisition workers claim requests routed by frozen CrawlPolicy transport, acquire one page, store
immutable raw HTML, and publish a frozen ingestion job. They never open DuckLake or wait for
ingestion, navigation, or materialization.

HTTP acquisition uses a lightweight asynchronous client. Browser acquisition uses one long-lived
Chromium runtime per process with bounded local page concurrency. External providers use the same
single-page output contract. Each transport has a distinct NATS subject and Kubernetes deployment
so it can scale independently.

CrawlPolicy concurrency is a deployment-wide remote-pressure ceiling enforced with expiring NATS
leases. Local browser concurrency protects one Chromium process; it is not the deployment-wide
remote limit.

### Ingestion workers

Ingestion workers verify raw objects, build bounded page-local DOM staging, create
navigation-critical system projections, commit document/crawl/element evidence, publish a verified
navigation package, evaluate outgoing bounded edge SQL, and admit returned URLs.

They may publish asynchronous materialization scope notifications after the base commit. They never
evaluate or commit user materializations and never wait for them.

### Materialization workers

Materialization workers own live materialization CDC discovery, activation backfill, bounded scope
evaluation, deterministic staging, scope replacement, durable coverage, dematerialization, and lag
settlement. Saved queries cannot be materialized; a view may have at most one live materialization.

Activation declares whether updates are keyed by document or crawl and which returned column is the
incremental discriminator. Atlas does not offer manually refreshed materializations. An ineligible
view is not activated and reports the exact unsupported construct or missing discriminator.

### Maintenance worker

The maintenance worker owns bounded compaction, flushing, retention, staging cleanup, and narrowly
defined repair. It runs as one replica by default under an expiring global lease and waits for hot
path ingestion and materialization commits to drain. It consumes no acquisition, ingestion,
materialization, or graph-edge capacity.

## DuckDB and concurrent writers

Every ingestion and materialization worker process embeds DuckDB and owns its DuckLake connection.
Start with one active catalogue operation per process. Never share a connection between independent
coroutines; the connection remains owned until its result is completely consumed or closed.

PostgreSQL-backed DuckLake permits multiple worker processes to operate on the same lake. Each
mutation still requires:

- deterministic identity derived from immutable input;
- an expiring NATS lease to suppress overlapping redelivery compute;
- a PostgreSQL advisory lock around durable identity resolution and commit;
- bounded transaction-conflict retries; and
- authoritative reconciliation after an ambiguous commit.

Horizontal replicas provide concurrency for unrelated work. They do not make overlapping writes
conflict-free.

Repository and materialization transactions annotate snapshots with bounded Atlas operation
identifiers. Write responses use the committing connection's `last_committed_snapshot()` rather
than the globally latest snapshot.

## Crawl graph execution

A graph trigger creates current `GraphRun` state in NATS and offers seed URLs to entry nodes. Each
admitted URL becomes independently claimable work on the NATS subject selected by its frozen
transport. After base ingestion and verified navigation publication, every outgoing edge evaluates
bounded crawl-scoped SQL and offers returned URLs to target nodes.

Raw HTML is the recovery authority. A missing navigation package is regenerated from retained HTML.
NATS owns current claims, deduplication, readiness, and at-least-once delivery. The complete graph
contract is [CRAWL_GRAPHS.md](CRAWL_GRAPHS.md).

## Materialization observability

JetStream consumer depth is a deployment diagnostic. User-facing freshness comes from authoritative
DuckLake coverage and fan-out state:

- each view reports its pending and failed scopes, stored size, and last successful settlement;
- each graph reports materialization lag attributable to its crawls; and
- stopping materialization increases those counters without changing graph-run completion.

Queue age is the primary autoscaling signal. Process liveness alone is not healthy if the owned
queue has stopped advancing.

## Storage and batching

Repository and materialization operations are bounded by item, row, byte, memory, and oldest-item
deadlines. Low-volume deployments flush on time; high-volume deployments flush on size. DuckLake
owns Parquet layout and compaction. Atlas does not create permanent per-crawl files.

Terminal ingestion and materialization failures enter bounded dead-letter administration paths.
Redelivery resolves durable identity before repeating a write.

## Reads and cache

Acquisition reads through the repository boundary. A cache hit requires matching normalized URL and
frozen `config_hash`, acceptable age and document-quality flags, a verified raw object, and current
DOM and quality recipes. Missing evidence is a miss. A stale structural or quality projection is
rebuilt from raw HTML through the same ingestion path.

Public point reads use document content IDs and crawl UUIDs. Local paths and physical Parquet paths
are implementation details.

## Explicit bounds

HTML size, DOM element count, staging bytes, ingestion batches, materialization scopes, NATS
envelopes, edge SQL results, remote pressure, per-process browser pages, graph-run state, and runaway
ceilings all have explicit limits. Limit failures are visible and terminal; durable state is never
silently truncated.

## Code ownership

- `backend/control/` owns editable PostgreSQL-backed graph, policy, query, view, and materialization
  definitions.
- `backend/runtime/` owns NATS-backed current graph execution, delivery, admission, deduplication,
  leases, progress, and capacity.
- `backend/workers/` owns acquisition, ingestion, materialization, and maintenance process
  lifecycles.
- `backend/actions/` owns page acquisition behavior; it must not gain navigation loops.
- `backend/repository/objects/` owns immutable content-addressed raw HTML.
- `backend/repository/ingestion/` owns ingestion delivery, validation, batching, health, and
  recovery.
- `backend/materialization/` owns live CDC discovery, bounded scope evaluation, commit, coverage,
  lag, and recovery.
- `backend/repository/catalogue/` implements DuckLake behind the repository boundary.
- `backend/repository/service.py` is the application-facing durable repository boundary.
- `backend/dom/` owns the versioned structural DOM projection.
- `backend/api/` and `backend/cli/` are thin adapters.
- `backend/db/` owns generic SQLAlchemy setup and Alembic migrations.

## Exact contracts

Architecture documents the target ownership and invariants. During the direct greenfield cutover,
implementation may temporarily lag the target; superseded routes, subjects, workers, and shared
connection paths must be deleted rather than retained as compatibility behavior.

- Worker and scaling contract: [WORKER_ARCHITECTURE.md](WORKER_ARCHITECTURE.md)
- Crawl graph contract: [CRAWL_GRAPHS.md](CRAWL_GRAPHS.md)
- View and materialization contract: [PUBLICATIONS.md](PUBLICATIONS.md)
- Crawl-policy trial contract: [TRIALS.md](TRIALS.md)
- Configuration and defaults: [`.env.example`](../.env.example)
- PostgreSQL models: [`backend/control/`](../backend/control/)
- Runtime state and queues: [`backend/runtime/`](../backend/runtime/)
- Repository messages: [`backend/repository/ingestion/queue.py`](../backend/repository/ingestion/queue.py)
- DuckLake tables: [`backend/repository/catalogue/schema.py`](../backend/repository/catalogue/schema.py)
- HTTP surface: the FastAPI-generated OpenAPI document
