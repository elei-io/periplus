# Worker architecture

Atlas deploys four worker roles:

| Worker | Input | Output | Scaling dimension |
|---|---|---|---|
| Acquisition | graph crawl/readiness/edge work | Immutable HTML, ingestion job, and graph readiness | CDP and traversal coordination |
| Ingestion | repository ingestion queue | Crawl/DOM catalogue evidence | Critical catalogue capacity |
| Materialization | live/backfill scope queues | Authoritative materialized scopes | Live/backfill catalogue capacity |
| Maintenance | maintenance triggers | Compaction and cleanup | Exclusive maintenance capacity |

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
The durable crawl consumer uses a fixed, bounded worker-local look-ahead grouped by normalized
hostname. Buffered deliveries remain queued in crawl-request state and receive JetStream
heartbeats. A worker probes the deployment-wide domain permit before assigning a process-local
acquisition lane, so excess work for a saturated hostname cannot occupy every lane while another
hostname is ready. Ready hostnames are selected round-robin; a single-host backlog remains
work-conserving. Denied hostname probes receive a short worker-local cooldown, and nonblocking
capacity misses do not register Resource Governor waiters. Initial roots and each bounded edge
result preserve per-host order while being interleaved across hostnames before publication.
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

Materialization discovery directly publishes deterministic bounded scopes. One worker evaluates and
commits one scope through authoritative coverage. Crawl-triggered scope jobs retain the immutable
document identity already present in the crawl change so source SQL can bind crawl and document
predicates directly to physical scans. The shared DOM evidence table uses 256 document-hash buckets
so one scope prunes to a narrow physical partition without creating per-document files.
Maintenance is off-path and requires an exclusive background
catalogue and object-pressure permit. Once that bundle is waiting, new overlapping grants pause
briefly so existing holders can drain and maintenance cannot starve behind continuous object-only
work. Crawl-table CDC is a coalesced wake-up hint for compaction, never a maintenance work queue.
The worker debounces bursts, derives eligibility from authoritative DuckLake file metadata, and
rewrites at most one bounded table slice per exclusive permit. A periodic sweep remains the
recovery path when CDC is idle or unavailable, and outstanding debt retries without waiting for
the complete sweep interval.

Permits control shared pressure, operation leases suppress duplicate execution, and PostgreSQL
advisory locks fence commits. Nonblocking capacity probes are read-only on a miss. Granted permits
retry transient state contention while sufficient lease time remains instead of treating one
failed renewal CAS as loss. None of these mechanisms owns work delivery or workflow completion.

## Packaging and deployment

All roles use the same backend image and the same `atlas-worker <role>` entrypoint. Acquisition,
ingestion, materialization, and maintenance remain separate deployments so each retains independent
autoscaling, rollout, health, queue, and failure boundaries. Sharing packaging must not couple their
replica counts or cause one workload's backlog to add capacity to another workload.

Local Compose passes only addresses and credentials that differ inside its container network, plus
the small set of documented deployment safety rails. Runtime mechanics use validated code defaults;
they are not repeated as Compose interpolation knobs. Shared YAML anchors keep the remaining
catalogue, repository, NATS, and control-plane contracts consistent across roles.

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
