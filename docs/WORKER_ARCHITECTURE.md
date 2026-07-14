# Worker and resource architecture

Status: accepted target contract. [AUDIT.md](../AUDIT.md) records the direct implementation
cutover. Atlas does not support the superseded topology alongside this one.

Atlas separates two concerns that replica counts previously conflated:

1. **Capability scaling** determines how many processes can perform a kind of work.
2. **Resource admission** determines how much shared pressure may run at once.

Workers provide execution capacity. The Resource Governor protects remote sites, DuckLake, and
object storage across every replica and workload. Adding replicas can reduce a worker shortage, but
cannot silently raise a shared-resource ceiling.

## Product invariant

The graph-critical path remains:

```text
acquisition -> immutable content -> base ingestion -> HTML navigation or artifact terminal

                                         independent of

                            live materialization freshness
```

A slow or stopped materialization deployment may make views stale. It must not hold browser
capacity, delay base ingestion, prevent navigation readiness, or keep a graph run active.

## Deployment topology

```text
Postgres control plane
  graphs, policies, definitions, operational capacity configuration
                         |
                    graph runtime
                         |
                  durable NATS work
       +-----------------+------------------+
       |                 |                  |
 HTTP acquisition  browser acquisition  provider acquisition
       |                 |                  |
       +------ immutable HTML / artifacts ------+
                         |
                  catalogue ingestion
                         |
        DuckLake base evidence + navigation + edges
                         |
               asynchronous scope discovery
                         |
                  materialization scope
                         |
              DuckLake rows + durable coverage

Every expensive phase requests an expiring permit from:

Resource Governor -> remote-policy, catalogue, and object-store budgets

Maintenance -> exclusive background catalogue permit -> compaction / cleanup
```

HTTP, browser, provider, ingestion, and materialization remain separate failure and health domains.
They share resource budgets rather than a process, connection, or generic work consumer.

## Four different coordination mechanisms

These concepts must not be merged:

| Mechanism | Question answered | Authority |
| --- | --- | --- |
| Work message | What must eventually happen? | NATS JetStream |
| Capacity permit | May this resource pressure start now? | Resource Governor with expiring NATS KV state |
| Operation lease | Is this deterministic operation already executing? | NATS KV |
| Commit fence | May this durable identity commit safely? | PostgreSQL advisory lock plus DuckLake authority |

A capacity permit is performance and safety control, never durable completion state. If a worker
dies, its work redelivers and its permit expires. Correctness still comes from deterministic
identity, authoritative result lookup, operation leases, and commit fencing.

Atlas does not publish durable `lock.request`, `lock.acquired`, `lock.release`, or generic
`work.complete` workflows. A worker already holds the durable work message when it asks for
capacity. It acknowledges that message only after the next durable state exists.

## Resource Governor

The Resource Governor is a deliberately narrow admission controller. It owns allocation decisions,
not graph execution, workflow state, result settlement, or correctness.

The governor is a shared runtime contract implemented with compare-and-swap against one expiring,
revision-fenced NATS KV projection. It is not another service or leader-elected scheduler. Every
worker runs the same typed admission code, and KV revisions serialize competing grant updates.
Permit requests need not be durable because the original work message remains durable while a
worker waits and retries.

One request contains:

- the deterministic job identity;
- its fixed service class; and
- the complete set of simultaneously required resources and weighted units.

The governor grants a simultaneous resource bundle atomically or grants nothing. Workers must not
hold one permit while indefinitely waiting for another. Sequential phases release resources before
requesting the next phase's bundle.

Loss of NATS KV pauses new expensive work without losing queued work or corrupting durable state.
Existing grants remain bounded by their TTL and operation correctness does not depend on capacity
state being continuously available.

### Initial resource vocabulary

Keep the vocabulary closed until measurement proves another shared bottleneck:

| Resource | Meaning |
| --- | --- |
| `remote:<registrable-domain>` | Deployment-wide concurrent courtesy pressure for one website |
| `catalogue:hot` | Concurrent ingestion and materialization DuckLake operations; maintenance requests the full capacity for exclusivity |
| `object:read` | Weighted in-flight repository/object-store reads |
| `object:write` | Weighted in-flight repository/object-store writes |

Deployment setup creates five CrawlProfiles and one catch-all CrawlPolicy. Its remote permit is the
final match for every HTTP(S) URL. Evidence-backed trial applications and user policies add
more-specific matches above it. Runtime derives the permit key from the URL's registrable domain
and never invents an implicit transport or concurrency when policy resolution fails.

Browser page slots are process-local capacity and remain a local semaphore. The frozen CrawlPolicy
permit is deployment-wide remote pressure. They are deliberately different resources.

The HTTP acquisition boundary always accepts `text/html` and `application/xhtml+xml`. A frozen
HTTP CrawlProfile may additionally retain direct responses in the closed media families
`application/pdf`, `image/*`, and `video/*`, subject to policy and deployment byte ceilings. Other
media types settle as non-retryable acquisition warnings. Artifact bytes are streamed to bounded
local spool, stored exactly and content-addressed, and never enter DOM projection. Embedded HTML
subresources are not artifact crawl inputs. DOM traversal itself is iterative so valid deeply
nested HTML does not depend on the Python recursion limit.

S3 / MinIO is not a mutex. Object-store permits represent weighted operations or expected bytes.
Known byte sizes are used where available; bounded operation-class estimates are used otherwise.
Metrics compare estimates with actual bytes and latency. Atlas starts with static budgets and does
not add a self-tuning feedback controller before production measurements justify one.
An object budget must be at least as large as the biggest admitted bounded operation; startup/work
fails configuration validation instead of waiting forever for an impossible grant.

DuckLake is also not a single correctness lock. `catalogue:hot` is a capacity pool. Deterministic
operation leases and PostgreSQL advisory locks continue to fence overlapping identities.
Worker dependency probes do not wait behind a process's occupied embedded-DuckDB lane: active
owned work is healthy, while queue-progress monitoring detects a genuinely stuck operation.

### Fixed service classes

Priority policy is code-owned and intentionally small:

1. `critical` — ordinary graph acquisition persistence, base ingestion, and graph navigation;
2. `live` — current user materialization scopes;
3. `backfill` — historical materialization coverage;
4. `maintenance` — compaction, cleanup, and bounded repair.

The initial algorithm reserves catalogue shares in both directions so graph-critical work and user
materialization cannot starve each other, caps concurrent backfill, and grants maintenance the full
catalogue capacity only after hot grants drain. Workers retry unavailable
bundles while retaining their work messages. Do not add a central pending-request scheduler,
weighted-fair queue, or adaptive controller until measured starvation or unfairness requires one.

Users do not author scheduling algorithms. Typed deployment configuration supplies capacities,
critical/noncritical reserves, and the backfill ceiling. Per-remote CrawlPolicy limits remain editable
control-plane policy. Changing worker replicas does not change either contract.

## Queue surface

Queue subjects describe typed work and routing, not locks or generic workflow transitions. The
logical surface is:

```text
ATLAS_GRAPH_WORK
  atlas.graph.crawl.http
  atlas.graph.crawl.browser
  atlas.graph.crawl.provider.<name>
  atlas.graph.edge
  atlas.graph.readiness

ATLAS_CATALOGUE_WORK
  atlas.catalogue.ingest
  atlas.catalogue.materialize.live
  atlas.catalogue.materialize.backfill

ATLAS_DEAD_LETTER
  atlas.dead_letter.<work-class>
```

Separate subjects preserve independent consumers, failure domains, and health while keeping the
message protocols small. A generic catalogue RPC that accepts arbitrary SQL is forbidden.
Maintenance may remain timer-driven because it has one owner; it does not need a work queue merely
for symmetry.

## DuckDB and DuckLake process model

DuckDB remains embedded in Atlas workers; there is no central remote DuckDB session. Each ingestion
or materialization process owns its DuckDB/DuckLake connection and one local catalogue execution
lane initially. A result must be fully consumed or closed before ownership returns to that lane.

PostgreSQL-backed DuckLake permits unrelated processes to commit concurrently. Every mutation still
requires:

- deterministic identity derived from immutable input;
- an expiring operation lease to suppress overlapping redelivery compute;
- a PostgreSQL advisory lock around durable identity resolution and commit;
- bounded transaction-conflict retries; and
- authoritative reconciliation after an ambiguous commit.

The Resource Governor bounds combined pressure across those independent processes. It does not
replace any correctness mechanism above.

## Acquisition workers

Acquisition workers own only one remote page acquisition:

1. claim a frozen request from the subject for its transport;
2. request the frozen policy's remote permit and any simultaneous local transport capacity;
3. load one page;
4. release remote/browser pressure;
5. request weighted object-write capacity and store immutable raw HTML or artifact bytes; and
6. publish a frozen ingestion job before acknowledging acquisition.

They never open DuckLake, parse retained DOM, evaluate edges, wait for ingestion, or run
materialization.

HTTP workers use a process-wide asynchronous client and bounded local I/O concurrency. Browser
workers own one long-lived Chromium runtime with bounded page/context concurrency. Providers receive
their own subject and deployment when their quotas or scaling differ. Browser and provider profiles
produce raw HTML; they do not claim artifact support unless they can preserve original
main-response bytes exactly.

## Ingestion workers

Ingestion is `critical` catalogue work. One frozen job:

1. resolves any already-committed deterministic identity;
2. verifies immutable raw HTML or artifact bytes;
3. builds page-local DOM staging and navigation-critical system projections for HTML only;
4. requests an atomic catalogue/object-store permit bundle;
5. commits crawl, document, element, projection, and provenance evidence;
6. writes and verifies the bounded navigation package for HTML;
7. publishes crawl readiness;
8. evaluates outgoing bounded SQL edges for HTML, while artifacts settle terminally; and
9. admits returned URLs as new transport-routed CrawlRequests.

Ingestion does not evaluate or commit user materializations. It does not poll maintenance state;
the governor's critical reservation and maintenance exclusivity arbitrate shared resources.

## Materialization workers

Materialization discovery consumes crawl CDC and activation backfill boundaries and publishes
deterministic scope jobs directly. There is no fan-out header/member ledger and no separate result
commit queue.

One materialization scope job owns the complete recoverable unit:

1. validate the active definition revision and scope contract;
2. request `live` or `backfill` catalogue/object-store capacity;
3. acquire the deterministic operation lease;
4. evaluate one bounded scope or reuse verified deterministic staging;
5. atomically replace that scope and record authoritative coverage;
6. resolve an ambiguous commit from coverage; and
7. acknowledge the original scope message.

CDC and backfill may discover the same scope. They publish the same deterministic identity, and
successful `materialization_scope_results` coverage is the sole completion authority. Missing work
and user-visible lag are derived as eligible scopes minus successful coverage.

Compute and commit are phases of one job, not independently scalable queues. Scaling materialization
replicas adds available executors but cannot exceed the governor's live catalogue and object-store
allocation. Materialization remains independent from ingestion so a stale definition, poison scope,
or native failure cannot take down graph-critical work.

Dematerialization is a separately fenced lifecycle operation. It first disables discovery, then
drops the managed table and coverage under catalogue admission and commit fencing.

## Maintenance worker

Maintenance owns bounded compaction, eligible flushing, retention, staging cleanup, and narrowly
defined repair. It runs as one replica by default.

Before a mutating operation it requests the complete `catalogue:hot` capacity as `maintenance`. The
governor grants only after existing hot catalogue permits drain. An operation lease suppresses
duplicate execution and the PostgreSQL maintenance fence protects correctness. There is no separate
maintenance-active polling protocol or bespoke maintenance-capacity ledger.

## Acknowledgement fences

Delivery is at-least-once. Workers acknowledge only after the next durable state exists:

```text
acquisition ACK     raw HTML/artifact bytes and ingestion publication are durable
ingestion ACK       base evidence and recoverable navigation readiness are durable
materialization ACK scope replacement and successful/terminal coverage are durable
edge ACK            every admitted target request is durable or terminal
```

Capacity release is not part of this durability chain. Explicit release improves utilization; TTL
expiry is the final recovery boundary.

## Health, metrics, and scaling

Infra scales capability deployments from queue age and local saturation. It changes shared-resource
budgets only after DuckLake, object-store, or remote-capacity evidence supports the change.

| Deployment | Primary scaling signal | Shared-resource guard |
| --- | --- | --- |
| HTTP acquisition | oldest HTTP work age, local I/O saturation | remote-policy and object-write permits |
| Browser acquisition | oldest browser work age, page-slot saturation | remote-policy and object-write permits |
| Provider acquisition | oldest provider work age, provider latency | remote-policy/provider quota permits |
| Ingestion | oldest ingestion age, local CPU/memory | reserved critical catalogue/object permits |
| Materialization | oldest live/backfill scope age, local CPU/memory | live/backfill catalogue/object permits |
| Maintenance | scheduled upkeep age | exclusive maintenance permit |

Admission metrics include permit wait age, grants, expirations, utilization by resource and class,
and actual versus estimated object I/O. Queue age remains more important than raw depth. CPU and
memory are local executor signals, not correctness state.

Adding replicas must be monotonic: it may reduce executor shortage, but it cannot increase shared
pressure beyond the configured budget. Replicas waiting almost entirely for permits indicate a
resource bottleneck, not a reason to add more workers.

## Production deployment

```text
atlas-api
atlas-crawl-http-worker                 autoscaled
atlas-crawl-browser-worker              autoscaled
atlas-crawl-provider-<name>-worker      autoscaled when configured
atlas-ingestion-worker                  autoscaled
atlas-materialization-worker            autoscaled
atlas-maintenance-worker                replicas: 1
```

## Direct greenfield cutover

The implementation changes as one incompatible architecture cutover. Do not deploy a long-lived
mixed topology.

1. Introduce and prove the typed KV-backed governor contract with one active caller.
2. Replace CrawlPolicy-specific capacity storage with governor remote permits.
3. Put ingestion, materialization, and maintenance behind catalogue/object admission.
4. Collapse materialization compute and commit into one scope delivery contract.
5. Delete fan-out tables, plan messages, settlement, repair, and the commit stream.
6. Delete bespoke maintenance admission, maintenance polling, old streams, consumers, and obsolete
   configuration.
7. Reset disposable NATS and DuckLake development state when the contract changes.
8. Provision resource-grant KV state before workers and prove the complete path with bounded work.

There are no dual publications, fallback subjects, compatibility consumers, legacy aliases, or
migration bridges. [AUDIT.md](../AUDIT.md) is the sole checklist for implementation gaps.

## Required proof before production

- Materialization stopped: acquisition, ingestion, navigation, and graph completion continue.
- Materialization scaled from one to many replicas: granted catalogue concurrency remains capped.
- Ingestion retains its reserved critical share under continuous materialization backlog.
- NATS/KV interruption and worker restart: durable work remains and permit acquisition resumes
  without corrupting state.
- Worker death before execution, during staging, during commit, and after commit-before-ACK
  converges to one durable effect.
- MinIO/S3 throttling raises permit and queue age without uncontrolled request growth.
- Remote pressure remains bounded across every acquisition replica.
- Backfill cannot starve live work; live work cannot consume the critical ingestion reservation.
- Maintenance never overlaps a granted hot catalogue operation.
- A poison materialization scope reaches dead letter without crash-looping ingestion.
- Metrics distinguish executor shortage from remote, catalogue, and object-store saturation.
- The crawl dashboard reconstructs peak per-site concurrency from durable crawl timing,
  `url_registrable_domain`, and frozen `remote_concurrency`; 100% means that site reached its limit.
- Run warnings count external acquisition failures. Run errors count Atlas pipeline/lifecycle and
  materialization failures. `Cooldown` is a presentation phase while terminal acquisition waits
  for materialization coverage, not a second graph-run terminal state.
