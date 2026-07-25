# Worker architecture

Atlas deploys five worker roles:

| Worker | Input | Output | Scaling dimension |
|---|---|---|---|
| Acquisition | graph crawl/readiness/edge work | Immutable HTML, ingestion job, and graph readiness | CDP and traversal coordination |
| Ingestion | repository ingestion queue | Crawl/DOM catalogue evidence | Critical catalogue capacity |
| Catalogue ingress | Basin NATS DML/DDL CDC | Atlas per-table DML subjects and global DDL subject | One durable consumer per Basin stream |
| Materialization | Filtered catalogue DML subjects | Stable materialization tables | Live catalogue capacity |
| Housekeeping | periodic timer | Atlas staging and navigation retention | Bounded object-store capacity |

## Acquisition worker

Each delivery represents one page. The worker claims the request, obtains Atlas object-write
capacity, obtains the URL's domain concurrency permit, observes its minimum request interval,
connects to the configured CDP endpoint, navigates, applies the enabled content-completion moves,
captures final HTML or an accepted artifact, stores it, and publishes ingestion work. Branch nodes
then derive a bounded navigation package and transactionally checkpoint readiness; leaf nodes
checkpoint readiness without generating a package. Page-only edges execute in standalone memory-limited DuckDB
connections. Historical joins use a read-only DuckLake connection at the run's pinned snapshot.
Each worker process owns one Playwright driver, while every delivery opens and closes its own CDP
connection and page so crawl state is not shared between deliveries.
The durable crawl consumer uses a fixed, bounded worker-local look-ahead grouped first by graph run
and then normalized hostname. Initial roots and edge traversal share a bounded acquisition window
per run; a durable root cursor refills released slots, so one
run cannot place an unbounded backlog ahead of later work. Buffered deliveries remain queued in
Postgres crawl-request state and receive JetStream
heartbeats. A worker probes the deployment-wide domain permit before assigning a process-local
acquisition lane, so excess work for a saturated hostname cannot occupy every lane while another
hostname is ready. Ready hostnames are selected round-robin; a single-host backlog remains
work-conserving. Shared response health cools a hostname after 429 or repeated 5xx responses before
the worker probes its distributed permit. Denied hostname probes receive a short worker-local
cooldown. Each hostname owns an independent NATS state key, so unrelated sites never contend on
global capacity state. Initial roots and each bounded edge
result preserve per-host order while being interleaved across hostnames before publication.
Deferred edge evaluation retains a bounded selected-URL package under the run's navigation prefix;
subsequent deliveries reuse it instead of reopening the source package or rerunning DuckDB.
Worker presence is renewed before recovery bookkeeping and retries transient NATS failures with
bounded backoff. Queue health uses the durable crawl consumer counters; run and node progress is
aggregated from indexed Postgres request rows. An independent heartbeat reports actual event-loop liveness. Unexpected
termination of any of these background runtimes fails the process so deployment supervision can
restart it.
When every method is disabled it sends no browser-only script or page-completion commands and
performs no page evaluation or interaction. It then ACKs. It never opens DuckLake for acquisition
or chooses a provider; historical edge evaluation is a separate bounded read-only navigation
operation in the same worker role.

There is one acquisition deployment and one durable crawl consumer. The CDP service owns the browser
farm, transport selection, proxy/profile concerns, and external scaling. Atlas retains website
politeness, response handling, retry evidence, and content correctness. It must not recreate
provider-specific queues, browser slots, direct HTTP clients, or transport fallback logic.

## Ingestion worker

The ingestion worker verifies immutable HTML, prepares DOM data, commits base evidence plus public
`crawl_attempts` and `crawl_steps` rows in one DuckLake transaction, records terminal ingestion state, and ACKs. It
does not publish graph readiness or alter traversal status. It does not calculate quality flags;
periodic analysis derives quality findings from committed step and element rows. One process owns
four independent session-affine DuckBasin clients and runs four ingestion lanes. Each client is
serialized; lanes prepare and commit independently. Arrow and Parquet staging data stream through
Quack; the worker has no lake S3 credentials. Operation leases suppress duplicate durable work;
Atlas does not hold PostgreSQL advisory locks across the remote commit.

Ingestion and materialization publish one atomic presence record per process. The record retains
every configured client lane and reports each as starting, available, active, or unavailable.
Lane-local session, dependency, and operation failures reduce usable capacity without removing the
lane or overwriting another lane's state. Process readiness aggregates event-loop liveness,
supervised background tasks, presence publication, and the requirement that at least one lane is
usable. A busy lane remains healthy. The capacity API distinguishes configured, usable, active,
and degraded lanes instead of treating a transient lane probe failure as zero configured capacity.
Queue throughput is a separate signal: no-progress age can alarm without poisoning lane usability,
and the capacity API reports pending, ACK-pending, redelivered, and quiescent redelivery-wait counts
independently. Per-lane telemetry reports the current operation duration, last successful commit,
client and token generations, remints, readiness-circuit state, and recovery reason.

The ingestion durable's `max_ack_pending` is one maximum batch per local client lane. During
shutdown, fetched or prepared work that cannot have entered a DuckLake commit is NAKed immediately.
Only a delivery whose commit call was entered is left for its ACK timeout, because its durable
outcome may be uncertain and must be reconciled on redelivery.

The four clients share one process-owned, single-flight OAuth token provider while retaining
independent Quack sessions. Provider throttling and outages apply jittered exponential backoff and
honour `Retry-After`; Quack authentication rejection invalidates the affected token generation.
A process-local DuckBasin circuit stops new pulls after typed transport or provider failures,
NAKs known-uncommitted deliveries with a bounded delay, admits one half-open probe, and restores
the remaining lanes one successful probe at a time. These infrastructure failures never consume a
job's deterministic processing-failure budget. Because native DuckDB calls cannot be cancelled
safely, a call that exceeds the code-owned hard deadline terminates the process non-zero so the
deployment supervisor replaces every client.

## Catalogue ingress, materialization, and housekeeping

The catalogue ingress reads one Basin-owned global DML stream and one Basin-owned global DDL
stream. It resolves physical table IDs through public DuckLake metadata over a minted Basin session.
Table-specific fan-out exists only in Atlas JetStream; the ingress ACKs a Basin message only after
JetStream confirms each deterministic Atlas publication.
Materialization workers own filtered durable NATS consumers. They create the
consumer before bootstrap, pause by stopping pulls, and coalesce ticks into
transactional keyed replacement, idempotent append, or explicit full refresh.
Concurrent DuckLake transaction and compaction conflicts receive bounded local retries. Persistent
conflicts leave bootstrap state unchanged or NAK live ticks for redelivery; they do not fail the
incarnation. Other failures retain their Postgres checkpoint and can be explicitly retried, while
incompatible source or target DDL remains a terminal schema block for that incarnation.
Keyed and append bootstrap is incremental: the worker first creates an empty private target, then
interleaves live CDC refreshes with restart-safe historical hash partitions. Each partition is one
bounded transaction, partial rows remain private in DuckLake, and Postgres stores the next
partition cursor. The stable public view switches to the target only after historical coverage is
complete. Explicit full refresh definitions retain whole-table bootstrap semantics.
Each immutable materialization incarnation owns a UUID-derived private table name under
`_atlas_materializations`; the stable public view name is only a wrapper and never doubles as the
physical table name.
One process owns eight session-affine clients. A stable materialization-ID shard assigns each
definition to one client lane, allowing unrelated materializations to refresh concurrently without
sharing a DuckDB connection.
Composite keys come from native `ducklake_table_changes(...)` queries bounded to the retained NATS
snapshot range. Keyed and append refreshes replace every direct scan of their declared driving
table with a changed-key-scoped scan before executing joins, macros, aggregates, or other query
work; the final result-key predicate remains only a correctness fence. Incremental definitions must
therefore directly reference their driving table and anchor large dependent scans beneath that
scoped relation. Materialization workers do not consume Basin CDC directly. The target table
identity remains stable.
Compiler definitions and physical metadata are read inside one short, session-affine remote
transaction. Unrelated materialization DML may advance the lake between reads, but cannot create a
mixed compiler snapshot or permanently fail an incarnation; residual snapshot contention is
retried from the persisted materialization cursor.
Schema boundaries block the incarnation instead of being crossed implicitly.

DuckBasin owns compaction, old-file cleanup, snapshot retention, and every other physical-lake
maintenance operation. Atlas housekeeping never opens DuckLake or consumes catalogue ticks. It
removes abandoned local ingestion staging files and lists only `runtime/navigation/` objects,
deleting one bounded batch after a terminal run's navigation grace period or after the orphan
cutoff when no run exists.
This is the authoritative retention path for S3-compatible providers; Atlas does not require bucket
lifecycle-policy APIs.

Acknowledgement follows the meaning of each input. Acquisition and ingestion
ACK commands only after their durable effect and terminal operational state are
recorded. A materialization ACKs coalesced ticks only after its target
transaction commits. Housekeeping is timer-driven and has no work-message acknowledgement.
These contracts are intentionally distinct and are not hidden behind a generic
worker-handler protocol.

Per-domain permits enforce cross-replica website concurrency and operation leases suppress duplicate
execution. Before an ingestion commit, Atlas resolves existing immutable URL, document, and artifact
identities in one bounded DuckLake lookup. It then leases the exact requests and still-missing
canonical identities, plus any existing document undergoing projection replacement, in stable
order. The fenced transaction re-reads every identity authoritatively, so concurrent batches that
both preflight a missing identity still serialize while common navigation URLs already in the
catalogue produce no lease traffic. Metrics count skipped and leased identities by kind so the
reduction remains visible during load tests.
Catalogue and object-store concurrency are bounded by owning process pools; Basin owns remote
DuckLake admission. None of these mechanisms owns work delivery or workflow completion.

## Packaging and deployment

All roles use the same backend image and the same `atlas-worker <role>` entrypoint. Ingress,
materialization, ingestion, and housekeeping share the same process shell for
signals, health, metrics, event-loop heartbeat, task supervision, and draining.
Acquisition uses the same lifecycle primitives inside its specialized
hostname-dispatch and navigation runtime. Acquisition,
ingestion, catalogue ingress, materialization, and housekeeping remain separate deployments so each retains independent
autoscaling, rollout, health, queue, and failure boundaries. Sharing packaging must not couple their
replica counts or cause one workload's backlog to add capacity to another workload.

Local Compose passes only addresses and credentials that differ inside its container network, plus
the small set of documented deployment safety rails. Runtime mechanics use validated code defaults;
they are not repeated as Compose interpolation knobs. Shared YAML anchors keep the remaining
catalogue, repository, NATS, and control-plane contracts consistent across roles.
Each S3 client derives its connection-pool size from the concurrency of its owning process workload;
there is no independent pool-size deployment setting.

Atlas accepts a literal NKey seed through `ATLAS_NATS_SEED` for application state and a separate
`DUCKBASIN_NATS_SEED` only for managed CDC ingress. Every Atlas-managed
JetStream stream and KV bucket declares an explicit positive `max_bytes`. Startup enforces those
bounds when attaching to pre-provisioned infrastructure and never reconciles an omitted bound to
unlimited storage. Atlas's normal namespace credentials use account-wide `>` permissions inside a
dedicated NATS account because JetStream KV data uses `$KV.*` wire subjects; account isolation is
the security boundary, not an `atlas.>` permission filter.
Consumer reconciliation is also startup-only: Atlas reads an existing durable with
`consumer_info()` and creates it when absent. The ingestion worker also reconciles its mutable ACK
wait, delivery ceiling, and delivery-attempt limits to the current code-owned contract; immutable
subject or acknowledgement-policy mismatches fail closed. API requests and scheduler ticks reuse
the API process's initialized graph-runtime handles and never provision JetStream.

API replicas also use the file-backed `atlas_catalogue_queries` KV bucket for active and recent
interactive-query status and cross-replica cancellation. It retains one revision per query for one
hour by default, is capped at 64 MiB, and uses the configured operational-state replica count.
Query result data never enters NATS; Arrow IPC flows directly from the executing API response.
