# Architecture

Status: accepted target contract. Atlas has one page-acquisition contract, one durable ingestion
path, one graph-driven navigation model, and one shared resource-admission model.

The central operating rule is:

> Scale capabilities; govern shared resources.

Worker replicas supply execution capacity for a capability. The Resource Governor independently
bounds pressure on remote sites, DuckLake, and S3 / MinIO across all workers. Replica counts are
never used as the only shared-resource control.

```text
Postgres control plane
  graphs, policies, definitions
             |
        graph runtime
             |
      NATS durable work
       /           \
acquisition     catalogue work
HTTP/browser/   ingestion (critical)
provider        materialization (live/backfill)
       \           /
       repository objects ---- DuckLake
          raw/staging         evidence/coverage

All expensive phases -> Resource Governor -> expiring resource permits
Maintenance ----------> exclusive background permit
```

The detailed contract is [WORKER_ARCHITECTURE.md](WORKER_ARCHITECTURE.md). Crawl graph semantics are
defined in [CRAWL_GRAPHS.md](CRAWL_GRAPHS.md), and live materialization in
[PUBLICATIONS.md](PUBLICATIONS.md).

## Critical-path invariant

```text
acquire -> retain immutable content -> ingest base evidence -> HTML navigation or artifact terminal

                                      independent of

                         materialization scope freshness
```

User materialization is asynchronous. A slow or failed materialization deployment may make a view
stale, but cannot hold a browser, delay base ingestion, block outgoing edges, or keep a graph run
active. Ingestion publishes a verified, page-local navigation package independently of every
user-owned catalogue view and materialization.

## State ownership

| Owner | Authoritative state | Must not own |
| --- | --- | --- |
| PostgreSQL `atlas` | Editable graphs, policies, schedules, matches, schemas, queries, views, materialization definitions, and lifecycle intent | Current graph execution, crawl history, HTML, DOM evidence |
| NATS JetStream | Durable current work, graph delivery, retries, dead letters, and at-least-once queue state | Irreplaceable analytical history or large payload bytes |
| NATS KV | Graph runs, requests, admission, deduplication, progress, worker presence, operation leases, and expiring resource grants | Materialization completion or other analytical truth |
| Repository objects | Immutable content-addressed raw HTML and artifacts, bounded navigation packages, and deterministic temporary staging | Mutable workflow or scheduling truth |
| DuckLake | Documents, artifacts, crawls, graph provenance, DOM, materialized rows, snapshots, and authoritative scope coverage | Editable graph topology or current queue state |
| Resource Governor | Current resource-allocation decisions | Work delivery, workflow completion, or commit correctness |
| Prometheus | Counters, gauges, histograms, and capacity evidence | Correctness-critical state |

No state is independently authoritative in two stores. Materialization completion is successful
scope coverage in DuckLake, not a plan header, settlement counter, queue position, or metric.

## Work, capacity, and correctness

Atlas uses four separate mechanisms:

- a JetStream work message says what must eventually happen;
- a Resource Governor permit says whether shared pressure may start now;
- an operation lease suppresses simultaneous execution of one deterministic operation; and
- a PostgreSQL advisory lock plus authoritative DuckLake lookup fences durable commit.

Capacity permits are expiring operational state. They do not prove that work started, completed, or
committed. A worker holds the original durable message while requesting an atomic permit bundle,
publishes the next durable fact, acknowledges the message, and releases the permit. TTL expiry
recovers capacity after process loss.

Atlas has no durable lock-request/acquired/release workflow and no central scheduler that receives
every completion. NATS owns work delivery; the Governor owns only admission.

## Capability and resource boundaries

The independently scalable capabilities are:

- HTTP acquisition;
- browser acquisition;
- optional provider acquisition;
- base ingestion and graph navigation; and
- live/backfill materialization.

Maintenance and the Governor are fixed-size operational deployments. Ingestion and materialization
remain separate processes because their criticality and failure modes differ, even though both use
DuckLake and object storage.

Shared resources are governed across those capabilities:

- `remote:<registrable-domain>` bounds courtesy concurrency independently per website;
- process-local browser slots protect one Chromium runtime;
- `catalogue:hot` bounds combined ingestion/materialization operations;
- maintenance requests the complete `catalogue:hot` pool after ordinary work drains; and
- weighted `object:read` and `object:write` budgets bound S3 / MinIO pressure.

Deployment setup seeds five CrawlProfiles and one editable catch-all CrawlPolicy for `*://*/*`.
Every admitted URL resolves that policy or a more-specific policy added by a trial or user. The
governor key is derived from the URL's registrable domain; there is no operator-authored domain
group, implicit acquisition profile, or `unclassified` lane.

The fixed service classes are `critical`, `live`, `backfill`, and `maintenance`. Critical graph work
and ingestion have a reserved catalogue share, live/backfill work has a reciprocal reserved share,
backfill has an explicit concurrency ceiling, and
maintenance requests the full catalogue pool. Scheduling rules are code-owned. Capacities and class
bounds are typed deployment configuration, while per-remote limits remain frozen CrawlPolicy data.

## Acquisition

A frozen CrawlPolicy and its CrawlProfile route each request to its transport subject. The worker acquires the
deployment-wide remote permit, loads one URL, releases remote/browser pressure, obtains bounded
object-write capacity, stores immutable HTML or an explicitly allowed direct-response artifact,
and publishes a frozen ingestion job.

Acquisition workers never open DuckLake or wait for ingestion, navigation, materialization, or
maintenance. HTML remains the shared transport contract. Exact artifact capture is enabled only
for transports that can preserve the original main-response bytes; initially that is HTTP.

## Ingestion and graph continuation

Ingestion is graph-critical catalogue work. For HTML it verifies raw HTML, builds bounded page-local
DOM and the navigation payload, commits base evidence, publishes a verified navigation package, and
evaluates outgoing bounded SQL edges. For an artifact it verifies the immutable bytes, commits the
artifact and crawl observation, and marks the request terminal without DOM or outgoing edges.

Raw HTML is the regeneration authority. NATS owns current readiness and redelivery; DuckLake owns
durable evidence. Ingestion never evaluates or commits user materialization scopes.

## Materialization

Crawl CDC and activation backfill discover eligible document- or crawl-scoped work and publish
deterministic scope jobs directly. One job covers bounded evaluation, optional deterministic
staging, atomic scope replacement, and coverage recording. Compute and commit are not separate work
queues.

There are no fan-out headers, fan-out members, plan settlement, or startup repair of missing plans.
CDC and backfill may rediscover the same scope because the job identity is deterministic and
successful `materialization_scope_results` coverage is authoritative.

Missing work and lag are derived as:

```text
eligible active-definition scopes
MINUS
successful materialization scope coverage
```

Materialization workers request `live` or `backfill` catalogue/object capacity. Adding replicas
cannot exceed those shared budgets or consume the critical ingestion reservation.

## Maintenance

Maintenance owns bounded compaction, eligible flushing, retention, staging cleanup, and narrowly
defined repair. It requests an exclusive background catalogue permit. The Governor grants it only
after hot permits drain; an operation lease and PostgreSQL maintenance fence still protect
correctness.

There is no bespoke maintenance-active polling protocol. Maintenance is neither hidden in request
paths nor allowed to consume graph-critical admission.

## DuckDB and concurrent writers

Every ingestion and materialization process owns its embedded DuckDB connection and local execution
lane. Connections are never shared between independently scheduled work.

Horizontal safety requires deterministic operation identity, an expiring operation lease, advisory
commit fencing, bounded conflict retry, and authoritative ambiguous-commit reconciliation. The
Resource Governor adds a global pressure ceiling; it does not replace those requirements.

Transactions report the committing connection's `last_committed_snapshot()`, never an unrelated
globally latest snapshot.

## Queue and acknowledgement contract

The target work surface has transport-specific graph subjects and class-specific catalogue
subjects. Subjects route typed work; they do not model locks. A generic catalogue RPC that executes
arbitrary SQL is forbidden.

At-least-once acknowledgement fences are:

```text
acquisition ACK     immutable HTML/artifact bytes and ingestion publication are durable
ingestion ACK       base evidence and recoverable navigation readiness are durable
materialization ACK scope replacement and coverage are durable
edge ACK            target request publication is durable or terminal
```

Terminal failures enter bounded dead-letter administration. Redelivery resolves authoritative
identity before repeating expensive work or writes.

## Scaling and observability

Queue age and local saturation scale capability deployments. Permit wait age and budget utilization
identify shared-resource bottlenecks. Adding replicas is safe but may not increase throughput when a
shared budget is saturated.

Operators change DuckLake or object-store budgets only from measured capacity evidence. Atlas starts
with static budgets, fixed fairness, and explicit limits rather than an automatic feedback
controller. Process liveness is insufficient: each queue and governor resource reports progress,
wait age, grants, expirations, and saturation.

## Storage and explicit bounds

HTML and artifact size, DOM elements, staging bytes, ingestion batches, materialization scopes, messages, graph
state, SQL results, remote pressure, browser pages, catalogue concurrency, object I/O, and graph-run
ceilings all have explicit limits. Failures are visible; durable state is never silently truncated.

DuckLake owns Parquet layout and compaction. Atlas never creates permanent per-crawl or per-scope
files. Object keys are repository-relative and physical local or Parquet paths do not cross public
boundaries.

## Code ownership

- `backend/control/` owns editable PostgreSQL-backed definitions and lifecycle intent.
- `backend/runtime/` owns graph execution, work delivery, admission, operation leases, and the
  KV-backed resource-governance contract.
- `backend/workers/` owns transport, ingestion, materialization, and maintenance process
  entrypoints.
- `backend/actions/` owns page acquisition behavior and no traversal loops.
- `backend/repository/objects/` owns immutable raw HTML, artifacts, and bounded repository objects.
- `backend/repository/ingestion/` owns ingestion validation, batching, health, and recovery.
- `backend/materialization/` owns discovery and one-scope evaluation-through-coverage.
- `backend/repository/catalogue/` privately implements DuckLake.
- `backend/repository/service.py` is the application-facing durable repository boundary.
- `backend/dom/` owns versioned DOM projection.
- `backend/api/` and `backend/cli/` remain thin adapters.

## Direct cutover

Atlas is greenfield. The resource-governed contract directly replaced the former capacity,
fan-out, materialization-commit, and maintenance-admission paths. Do not add dual publications,
fallback consumers, legacy aliases, or migration bridges. Reset disposable NATS and DuckLake
development state when queue or schema contracts change, and delete superseded code and
configuration in the same cutover.

- Implementation checklist: [AUDIT.md](../AUDIT.md)
- Worker and resource contract: [WORKER_ARCHITECTURE.md](WORKER_ARCHITECTURE.md)
- Crawl graph contract: [CRAWL_GRAPHS.md](CRAWL_GRAPHS.md)
- Materialization contract: [PUBLICATIONS.md](PUBLICATIONS.md)
- Configuration defaults: [`.env.example`](../.env.example)
