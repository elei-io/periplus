# Hazards

These are failure patterns Atlas has encountered or is especially likely to encounter. Treat them
as design-review prompts, not as reasons to add preventive frameworks.

## Split ownership

**Putting graph-run state, crawl requests, or crawl history in Postgres.** It creates two execution
authorities and couples the control plane to high-volume work. Editable graph topology and policy
belong in Postgres, current runs and requests in NATS, and durable crawl history in DuckLake.

**Using DuckLake as the live graph frontier.** DuckLake records durable observations and coverage;
it does not own queued, claimed, seen, or admitted crawl requests. SQL edges discover candidates,
then NATS/KV owns current admission and delivery.

**Writing the same truth to multiple stores.** Mirrors eventually disagree and require repair
logic. Store one authoritative record and retain only identifiers or provenance elsewhere.

**Keeping compatibility paths after a migration.** Old task models, action primitives, aliases,
flags, routes, dual reads, and fallback queues make the graph cutover optional forever. Change the
contract directly and delete the superseded path.

**Treating metrics or progress as correctness state.** Prometheus and events may be missing or
duplicated. Claims, dedupe, ingestion results, and navigation package references live in
revision-fenced NATS state; referenced bytes are verified against S3 / MinIO size and digest.

## Accidental workflow platform

**Letting graph nodes run arbitrary compute.** A node accepts URL inputs and maps admitted inputs to
the single crawl acquisition primitive. Arbitrary scripts, connectors, transformations, and side
effects would turn Atlas into a general workflow orchestrator.

**Letting edges perform side effects.** An edge is bounded, crawl-scoped SQL that returns URL inputs
for a target node. It does not publish externally, mutate the catalogue, invoke APIs, or write
destination systems.

**Hiding traversal inside an action or node.** Pagination, search-result following, and site walking
belong in visible graph edges, including self-edges. Do not add an in-memory frontier or
action-specific crawl loop alongside graph execution.

**Conflating remote pressure with browser capacity.** The Resource Governor enforces each frozen
CrawlPolicy's deployment-wide remote-acquisition ceiling. Browser concurrency remains additionally
bounded inside each browser worker. Do not turn remote policy capacity into a global browser pool or
hold it during cache lookup, object persistence, ingestion, or navigation waiting.

**Routing every transport through one crawl subject.** A shared durable consumer cannot be scaled by
transport without claiming and rejecting work after reading its body. Freeze the transport before
publication and route HTTP, browser, and external-provider requests to distinct subjects with the
same raw-HTML output contract.

**Introducing a central browser RPC service without evidence.** It adds another session, timeout,
payload-transfer, and failure boundary. Separate browser acquisition as its own queue consumer and
deployment first. A remote browser protocol requires a measured need beyond independent scaling.

**Adding graph machinery for non-crawl workflows.** Crawl graphs solve a demonstrated acquisition
problem. They are not justification for generic node registries, arbitrary payload processors, or
destination integrations.

## Resource-governance mistakes

**Treating replica counts as shared-resource limits.** Replicas provide executors; they do not bound
combined DuckLake, S3 / MinIO, or remote pressure. Adding a materialization pod must not implicitly
raise catalogue concurrency or consume ingestion's reserved share. All expensive phases use the
shared Resource Governor.

**Turning reserved shares into permanent idle capacity.** A reserve is a minimum guarantee when its
class is waiting, not a hard ceiling on another class. Record only expiring admission intent, allow
idle units to be borrowed, and restore the waiting class's share as current grants drain.

**Counting admission waits as failed work.** Full capacity is normal queue backpressure. Heartbeat
the durable message while waiting; do not consume processing attempts, reconcile an operation that
never started, emit a traceback, publish dead letter, or fail worker health.

**Turning the Resource Governor into a workflow scheduler.** The governor decides only whether a
resource bundle may start. It does not create work, sequence graph nodes, receive every completion,
own materialization lag, or execute arbitrary catalogue RPC. JetStream remains the durable work
authority and DuckLake coverage remains analytical completion truth.

**Modeling permits as durable lock workflows.** A `lock.request -> lock.acquired -> work.request ->
work.complete -> lock.release` protocol creates races, repair paths, and another state machine. A
worker retains its original durable job while requesting an atomic expiring permit bundle. Explicit
release improves utilization; TTL expiry is recovery.

**Conflating capacity permits, operation leases, and commit fences.** A permit controls pressure, an
operation lease suppresses simultaneous duplicate execution, and a PostgreSQL advisory lock fences
durable identity resolution and commit. None substitutes for another, and a permit is never
completion state.

**Using one global DuckLake mutex.** Catalogue admission is a bounded capacity pool with an
exclusive maintenance mode, not a single correctness lock. Serialize one process-owned connection,
fence overlapping identities, and allow governor-approved unrelated work to proceed concurrently.

**Holding one scarce permit while waiting for another.** It creates distributed deadlock and poor
utilization. Request every simultaneously needed resource as one atomic bundle; release sequential
phase resources before requesting the next bundle.

**Adding self-tuning admission before measurement.** Feedback controllers can oscillate and conceal
the actual bottleneck. Begin with static typed budgets, fixed class fairness, permit-wait metrics,
and measured object bytes/latency. Automate adjustments only after production evidence defines a
stable control signal.

## Graph execution mistakes

**Blocking an acquisition worker on downstream processing.** A node invocation may be logically
awaiting ingestion or navigation publication, but the HTTP client or browser worker must be
released after raw HTML and the frozen ingestion job are durable. Ingestion resumes graph
evaluation; user materialization remains off the graph path.

**Putting ingestion in an acquisition worker.** DOM projection and DuckLake work consume CPU,
memory, and commit time while expensive browser capacity sits idle. Acquisition workers never open
DuckLake and never wait for base evidence to commit.

**Making graph completion depend on user materialization.** Base ingestion and verified navigation
readiness are the graph fence. Materialization may lag or fail independently and must affect view
freshness metrics rather than crawl-request or graph-run terminal state.

**Running edges before the crawl-ready fence.** Evaluate outgoing edges only after base ingestion
and verified navigation-package publication. Verify the package byte size and SHA-256 from its NATS
reference before registering `page.links`.

**Putting navigation bytes in NATS.** NATS owns package requirement, readiness, delivery, claims,
and deletion safety. Store Arrow bytes in S3 / MinIO and keep only their integrity and lifecycle
metadata in NATS.

**Leaking ephemeral navigation objects.** Delete the run prefix after terminal settlement and a
short grace period. Also configure an object-store lifecycle expiration so crash orphans cannot
accumulate indefinitely.

**Waiting forever for a full analytical batch.** Every repository batch needs an oldest-item
deadline in addition to item, row, and byte thresholds. One materialization scope owns compute
through commit and must not wait for a separate result batch or later message.

**Letting a background consumer die invisibly.** A task created beside a worker's main loop must
report failure through readiness and be joined or polled by the owning loop. A durable commit
followed by a lost ACK is not a new operation: redelivery resolves authoritative identity and
acknowledges the existing effect.

**Reconciling only the first page of missing work.** A backfill `LIMIT` without a keyset cursor can
rediscover the same scopes forever. Backfill advances through the complete bounded anti-join of
eligible scopes and successful coverage. Republication is safe because scope identities and
coverage are idempotent, but a planner must still make forward progress.

**Assuming exactly-once delivery.** NATS messages may be redelivered. Edge evaluation identities,
target-node request identities, admission counters, and queue publication must be idempotent so a
replayed readiness event cannot duplicate crawl work.

**Batching a node's URL inputs into one indivisible job.** A node may receive many URLs, but each URL
must remain independently claimable, retryable, observable, and attributable. A growing URL list is
runtime state in NATS, not a Postgres graph field.

**Encoding recursion controls in pagination-specific code.** Self-edges express recursion
generically. SQL defines intended termination, request identity prevents exact cycles, and
deployment hard ceilings stop runaway unique URLs. Crawl policy controls remote pressure, retries,
timeouts, and acquisition behavior—not graph work budgets.

**Relying on SQL `LIMIT` as the only resource bound.** Edge SQL should state its intended
cardinality, but catalogue execution still enforces hard row, byte, memory, and timeout ceilings.
The platform independently enforces non-user-configurable graph-run request and duration ceilings.

**Executing mutable queued input.** A published `CrawlRequest` freezes its graph run, target node,
request, effective crawl policy, and source crawl/edge provenance. Later control-plane edits do not
silently mutate already queued work.

## Storage mistakes

**Letting acquisition or maintenance workers perform hot-path catalogue publication.** Ingestion
workers own base evidence, navigation-package publication, and edges. Materialization workers own
user-view scope commits. Maintenance receives an exclusive background permit only after hot catalogue
grants drain.

**Combining ingestion and materialization in one process.** The workflows then share connection,
memory, scheduling, health, and failure boundaries. Materialization backlog can starve graph-critical
ingestion even if separate coroutines or queues are used. Deploy them independently and never share
their DuckDB connections.

**Sharing a DuckDB connection between concurrent tasks.** A connection is owned by one execution
lane until its query result has been fully consumed or closed. Ingestion and materialization each
use process-owned connections. Horizontal replicas provide executors, while the Resource Governor
caps their combined catalogue and object-store pressure.

**Opening one PostgreSQL fence session per batch member.** A batch with 100 independent content
identities can exhaust a 100-client server before it begins its DuckLake transaction. Acquire the
complete independently contended lock set in deterministic order through one session. Do not hash
the set into one batch identity: `{A, B}` and `{B, C}` must still contend on `B`.

**Reintroducing a central remote DuckDB session.** It couples unrelated writes and makes one compute
process the throughput and failure boundary. Ingestion and materialization workers use embedded
DuckDB against the shared PostgreSQL-backed DuckLake catalogue with deterministic operation
identity, advisory fencing, and bounded retries.

**Creating permanent Parquet per crawl.** Small files and application-owned layout fight DuckLake
compaction. Use bounded temporary staging and let DuckLake own physical data files.

**Holding a blocking CDC poll open in Postgres.** An embedded DuckDB/Postgres CDC listener can retain
an idle transaction and consume a pooled metadata connection while it waits. Use non-blocking reads
with an async delay, bound every embedded Postgres pool, and enable idle/lifetime reaping.

**Holding catalogue admission while waiting for a CDC consumer lease.** Atlas already elects one
planner owner with an operation lease. Open the upstream consumer with non-blocking lease policy,
release catalogue admission immediately on contention, and do not open the CDC catalogue at all
when no live definition needs discovery.

**Reporting the globally latest snapshot as a write result.** Another connection may commit between
the local transaction and lookup. Attribute writes with `last_committed_snapshot()` on the
committing logical connection; reserve a global latest lookup for reads.

**Blindly retrying an ambiguous remote commit.** Resolve ingestion and materialization identity from
authoritative DuckLake state before acknowledging NATS or retrying a response whose commit outcome
is unknown.

**Writing nondeterministic bytes under an idempotent staging key.** A materialization operation ID
names one immutable Arrow object. Managed materialization SQL must define stable row and aggregate
ordering so physical file-layout changes cannot produce different bytes for the same logical scope.

**Running DuckLake `CHECKPOINT` without a retention contract.** It bundles inlined-data flushing,
compaction, delete rewrites, snapshot expiration, scheduled-file cleanup, and orphan deletion. Atlas
may automate bounded flushing, compaction, and aged scheduled-file cleanup, but snapshot expiration
must be fenced by retained CDC consumer positions and orphan deletion remains explicit.

**Backing up DuckLake metadata and data files independently.** The Postgres catalogue identifies the
exact files belonging to each snapshot. Production recovery needs coordinated Postgres PITR or a
snapshot-aligned dump plus versioned or replicated object storage.

**Separating raw-object and analytical storage selection.** The stores can silently land in
different environments. One repository backend selection must configure both.

**Exposing physical paths.** Local and DuckLake physical paths change across deployments. Public
contracts use repository-relative object keys, document IDs, crawl IDs, and graph provenance.

**Passing worker-local staging paths between services.** It works in single-host development and
fails when workers move. Recoverable staging uses repository-relative keys in the configured object
store with integrity metadata verified by the same scope operation.

**Mutating content-addressed objects.** A hash identity is immutable. Different bytes under the same
identity are a conflict, never an update.

## Hidden and unbounded behavior

**Creating policy state while crawling.** A missing crawl policy uses the explicit simple default
transport. Calibration and policy changes are separate control-plane operations.

**Allowing unbounded work.** HTML, elements, staging, batches, messages, graph current state,
materialization scopes, edge SQL, remote pressure, browser concurrency, catalogue concurrency, and
object I/O all need explicit ceilings and clear failures.

**Hiding maintenance in request paths.** Compaction, deletion, dead-letter recovery, and large
rebuilds are explicit operator actions, not side effects of reads, graph triggers, or crawls.

## Repository complexity

**Putting behavior in API or CLI adapters.** It produces divergent implementations. Adapters
validate and translate; graph execution belongs in runtime, crawl owns acquisition, SQL owns derived
navigation, and repository modules own durable evidence.

**Preserving unused administrative surfaces.** Dashboards, metrics, models, and configuration with
no active operator or caller still impose maintenance cost. Delete them; version control is the
archive.

**Maintaining exhaustive prose inventories.** Lists of every field and environment variable drift.
Document ownership and invariants, then link to code and `.env.example` for exact implemented
contracts.

**Adding defensive layers without a demonstrated failure.** Factories, registries, provider
interfaces, and policy engines make the main path harder to read. Prefer visible control flow and
the smallest boundary that protects a real invariant.
