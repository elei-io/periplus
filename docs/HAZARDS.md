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

**Conflating remote pressure with browser capacity.** NATS enforces each CrawlPolicy's explicit
deployment-wide remote-acquisition ceiling. Browser concurrency remains additionally bounded inside
each worker, and replicas determine physical browser capacity. Do not turn the policy lease into a
global browser pool or hold it during cache lookup, ingestion, or navigation waiting.

**Adding graph machinery for non-crawl workflows.** Crawl graphs solve a demonstrated acquisition
problem. They are not justification for generic node registries, arbitrary payload processors, or
destination integrations.

## Graph execution mistakes

**Blocking a crawl worker on enrichment.** A node invocation may be logically awaiting ingestion or
navigation publication, but the browser worker must be released. Durable state and readiness
notifications resume edge evaluation; analytical materialization remains off the hot path.

**Running edges before the crawl-ready fence.** Evaluate outgoing edges only after base ingestion
and verified navigation-package publication. Verify the package byte size and SHA-256 from its NATS
reference before registering `nav.*`.

**Putting navigation bytes in NATS.** NATS owns package requirement, readiness, delivery, claims,
and deletion safety. Store Arrow bytes in S3 / MinIO and keep only their integrity and lifecycle
metadata in NATS.

**Leaking ephemeral navigation objects.** Delete the run prefix after terminal settlement and a
short grace period. Also configure an object-store lifecycle expiration so crash orphans cannot
accumulate indefinitely.

**Waiting forever for a full analytical batch.** Every repository and materialization batch needs
an oldest-item deadline in addition to item, row, and byte thresholds. A low-volume deployment must
eventually commit without manual flushing or a later message arriving.

**Letting a background commit consumer die invisibly.** A task created beside a worker's main loop
must report failure through readiness and be joined or polled by the owning loop. A durable
materialization commit followed by failed fan-out settlement is not a terminal message failure:
NAK it so the idempotent commit can be recognized and settlement retried.

**Reconciling only the first page of missing work.** A periodic `LIMIT` without a keyset cursor can
republish the same identities forever after the broker deduplication window expires. Reconciliation
must advance through the complete bounded result set and publish each missing crawl at most once per
planner lifecycle.

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

**Letting crawl or maintenance workers perform hot-path catalogue publication.** Catalog workers are
the only graph-execution processes that ingest, materialize, and evaluate edges. Maintenance uses a
separate queue and global lease so storage upkeep cannot consume catalog throughput.

**Reintroducing a central remote DuckDB session.** It couples unrelated writes and makes one compute
process the throughput and failure boundary. Catalog workers use embedded DuckDB against the shared
Postgres-backed DuckLake catalogue with deterministic operation identity and bounded retries.

**Creating permanent Parquet per crawl.** Small files and application-owned layout fight DuckLake
compaction. Use bounded temporary staging and let DuckLake own physical data files.

**Holding a blocking CDC poll open in Postgres.** An embedded DuckDB/Postgres CDC listener can retain
an idle transaction and consume a pooled metadata connection while it waits. Use non-blocking reads
with an async delay, bound every embedded Postgres pool, and enable idle/lifetime reaping.

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
fails when workers move. Cross-process staging uses repository-relative keys in the configured
object store with integrity metadata verified by the writer.

**Mutating content-addressed objects.** A hash identity is immutable. Different bytes under the same
identity are a conflict, never an update.

## Hidden and unbounded behavior

**Creating policy state while crawling.** A missing crawl policy uses the explicit simple default
transport. Calibration and policy changes are separate control-plane operations.

**Allowing unbounded work.** HTML, elements, staging, batches, messages, graph current state,
materialization scopes, edge SQL, admission, and browser concurrency all need explicit ceilings and
clear failures.

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
