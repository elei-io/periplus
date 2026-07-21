# Worker architecture

Atlas deploys five worker roles:

| Worker | Input | Output | Scaling dimension |
|---|---|---|---|
| Acquisition | graph crawl/readiness/edge work | Immutable HTML, ingestion job, and graph readiness | CDP and traversal coordination |
| Ingestion | repository ingestion queue | Crawl/DOM catalogue evidence | Critical catalogue capacity |
| Catalogue relay | DuckLake DML/DDL CDC | Durable per-table DML subjects and global DDL subject | Exactly two DuckDB connections |
| Materialization | Filtered catalogue DML subjects | Stable materialization tables | Live catalogue capacity |
| Maintenance | maintenance triggers | Compaction and cleanup | Bounded background capacity |

## Acquisition worker

Each delivery represents one page. The worker claims the request, obtains Atlas object-write
capacity, obtains the URL's domain concurrency permit, observes its minimum request interval,
connects to the configured CDP endpoint, navigates, applies the enabled content-completion moves,
captures final HTML or an accepted artifact, stores it, and publishes ingestion work. Branch nodes
then derive a bounded navigation package and publish readiness; leaf nodes publish readiness
without generating a package. Page-only edges execute in standalone memory-limited DuckDB
connections. Historical joins use a read-only DuckLake connection at the run's pinned snapshot.
Each worker process owns one Playwright driver, while every delivery opens and closes its own CDP
connection and page so crawl state is not shared between deliveries.
The durable crawl consumer uses a fixed, bounded worker-local look-ahead grouped first by graph run
and then normalized hostname. Initial roots and edge traversal share a bounded acquisition window
per run; a durable root cursor refills released slots, so one
run cannot place an unbounded backlog ahead of later work. Buffered deliveries remain queued in
crawl-request state and receive JetStream
heartbeats. A worker probes the deployment-wide domain permit before assigning a process-local
acquisition lane, so excess work for a saturated hostname cannot occupy every lane while another
hostname is ready. Ready hostnames are selected round-robin; a single-host backlog remains
work-conserving. Shared response health cools a hostname after 429 or repeated 5xx responses before
the worker probes its distributed permit. Denied hostname probes receive a short worker-local cooldown, and nonblocking
capacity misses do not register Resource Governor waiters. Initial roots and each bounded edge
result preserve per-host order while being interleaved across hostnames before publication.
Deferred edge evaluation retains a bounded selected-URL package under the run's navigation prefix;
subsequent deliveries reuse it instead of reopening the source package or rerunning DuckDB.
Worker presence is renewed before recovery bookkeeping and retries transient NATS failures with
bounded backoff. Queue health uses the durable crawl consumer counters instead of scanning every
request record, while an independent heartbeat reports actual event-loop liveness. Unexpected
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
`crawl_attempts` and `crawl_steps` rows under the catalogue fence, records terminal ingestion state, and ACKs. It
does not publish graph readiness or alter traversal status. It does not calculate quality flags;
periodic analysis derives quality findings from committed step and element rows. It is critical
work. One process owns one embedded DuckDB connection and runs one catalogue operation at a time.

## Materialization and maintenance

The catalogue relay owns one catalogue-wide tick-mode DML consumer and one
catalogue-wide DDL consumer. Their two embedded DuckDB connections preserve
independent lease identities and are closed concurrently during process
shutdown. Table-specific fan-out exists only in JetStream; the relay publishes
only after JetStream confirms each deterministic message.
Materialization workers own filtered durable NATS consumers. They create the
consumer before bootstrap, pause by stopping pulls, and coalesce ticks into
transactional keyed replacement, idempotent append, or explicit full refresh.
Composite keys come from stateless CDC queries bounded to the retained NATS
snapshot range; materialization workers do not own persistent DuckLake CDC
consumers. The target table identity remains stable.
Schema boundaries block the incarnation instead of being crossed implicitly.
Maintenance consumes one durable wildcard NATS subscription over catalogue DML
ticks; it never opens a DuckLake CDC consumer or discovers materialized tables
through Postgres. It is off-path and uses one catalogue unit plus object pressure proportional to
the effective bounded pass size (target file size times bounded output count, capped by the maximum
operation bytes). It never pauses new foreground grants or waits for global
quiescence; DuckLake transaction conflicts are retried with bounded backoff. Same-class resource
waiters retain arrival order, and waiting critical object work reclaims capacity from later
noncritical requests. A catalogue tick is a coalesced wake-up hint for compaction, never a
maintenance work queue.
Its periodic recovery sweep also lists only `runtime/navigation/` objects older than
`ATLAS_GRAPH_MAX_RUN_SECONDS` and deletes one bounded batch owned by terminal or expired-away runs.
This is the authoritative retention path for S3-compatible providers; Atlas does not require bucket
lifecycle-policy APIs.
The worker debounces bursts, derives eligibility from authoritative DuckLake file metadata, and
rewrites at most one bounded table slice per proportional permit before yielding. A periodic sweep
remains the recovery path when CDC is idle or unavailable, and outstanding debt retries without
waiting for the complete sweep interval.

Acknowledgement follows the meaning of each input. Acquisition and ingestion
ACK commands only after their durable effect and terminal operational state are
recorded. A materialization ACKs coalesced ticks only after its target
transaction commits. Maintenance records its local wake-up before ACKing ticks;
the periodic metadata sweep recovers a process death after that acknowledgement.
These contracts are intentionally distinct and are not hidden behind a generic
worker-handler protocol.

Permits control shared pressure, operation leases suppress duplicate execution, and PostgreSQL
advisory locks fence commits. Nonblocking capacity probes are read-only on a miss. Granted permits
retry transient state contention while sufficient lease time remains instead of treating one
failed renewal CAS as loss. None of these mechanisms owns work delivery or workflow completion.

## Packaging and deployment

All roles use the same backend image and the same `atlas-worker <role>` entrypoint. Relay,
materialization, ingestion, and maintenance share the same process shell for
signals, health, metrics, event-loop heartbeat, task supervision, and draining.
Acquisition uses the same lifecycle primitives inside its specialized
hostname-dispatch and navigation runtime. Acquisition,
ingestion, catalogue relay, materialization, and maintenance remain separate deployments so each retains independent
autoscaling, rollout, health, queue, and failure boundaries. Sharing packaging must not couple their
replica counts or cause one workload's backlog to add capacity to another workload.

Local Compose passes only addresses and credentials that differ inside its container network, plus
the small set of documented deployment safety rails. Runtime mechanics use validated code defaults;
they are not repeated as Compose interpolation knobs. Shared YAML anchors keep the remaining
catalogue, repository, NATS, and control-plane contracts consistent across roles.
Each S3 client derives its connection-pool size from the concurrency of its owning process workload;
there is no independent pool-size deployment setting.

Atlas accepts a literal NKey seed through `NATS_SEED` and applies it to every application NATS
connection; omitting it retains unauthenticated local-development behavior. Every Atlas-managed
JetStream stream and KV bucket declares an explicit positive `max_bytes`. Startup enforces those
bounds when attaching to pre-provisioned infrastructure and never reconciles an omitted bound to
unlimited storage.

API replicas also use the file-backed `atlas_catalogue_queries` KV bucket for active and recent
interactive-query status and cross-replica cancellation. It retains one revision per query for one
hour by default, is capped at 64 MiB, and uses the same configured operational-state replica count
as Resource Governor grants. Query result data never enters NATS; Arrow IPC flows directly from the
executing API response.
