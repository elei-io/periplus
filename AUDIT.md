# Resource-governed architecture cutover audit

This file is the single checklist for the gap between the implementation and Atlas's accepted
resource-governed worker architecture. Canonical contracts are:

- [Architecture](docs/ARCHITECTURE.md)
- [Worker and resource architecture](docs/WORKER_ARCHITECTURE.md)
- [Crawl graphs](docs/CRAWL_GRAPHS.md)
- [Catalogue definitions and materialization](docs/PUBLICATIONS.md)
- [Hazards](docs/HAZARDS.md)

Older combined-worker and independently scaled compute/commit plans are superseded. They are not
supported alternatives.

## Accepted direction

Atlas scales capabilities and governs shared resources:

- HTTP, browser, and provider replicas supply acquisition capacity;
- ingestion replicas supply graph-critical catalogue executors;
- materialization replicas supply live/backfill catalogue executors;
- one fixed maintenance deployment owns off-path upkeep; and
- a shared KV-backed Resource Governor contract bounds remote, DuckLake, and object-store pressure
  across every replica without adding a scheduler service.

NATS owns durable work. The Governor owns only expiring capacity grants. Operation leases suppress
duplicate execution, while PostgreSQL advisory locks and DuckLake authority fence commits.

Materialization discovery publishes deterministic scopes directly. One scope message covers bounded
evaluation through atomic replacement and coverage. Successful `materialization_scope_results` is
the sole completion authority; lag is eligible scopes minus successful coverage.

## Completed direct replacements

The cutover landed as one direct contract change:

- CrawlPolicy-specific capacity KV state was replaced by stable domain-group remote permits;
- every worker-side expensive caller uses the typed KV-backed Resource Governor;
- ingestion and materialization acquire atomic catalogue/object-store permit bundles;
- reciprocal catalogue reserves prevent graph-critical and materialization starvation, while an
  independent ceiling bounds backfill;
- maintenance-active polling and the bespoke global maintenance lease were replaced by exclusive
  governor admission plus existing correctness fencing;
- live/backfill discovery publishes deterministic materialization scopes without fan-out planning;
- one recoverable scope message owns compute through commit and authoritative coverage;
- the materialization commit stream and writer consumer were deleted;
- fan-out headers, members, storage, settlement, reconciliation, and repair were deleted;
- user-facing pending, failed, and lag values derive from active definitions and scope coverage;
- catalogue work and typed dead letters use the shared accepted stream topology; and
- obsolete configuration, Compose wiring, metrics, tests, and admin paths were removed.

No dual publication, fallback consumer, compatibility flag, or schema bridge remains. Existing
development NATS and DuckLake state must be reset when deploying this greenfield contract because
the old stream and table schemas are intentionally unsupported.

## Remaining deployment reliability gates

Before production, tests must prove:

- materialization can stop without affecting acquisition, ingestion, navigation, or graph completion;
- restarting materialization catches up from CDC/backfill discovery and authoritative coverage;
- increasing materialization replicas does not increase granted DuckLake concurrency;
- ingestion retains its reserved critical share during continuous materialization backlog;
- NATS/KV interruption and worker restart preserve durable work and permit acquisition resumes safely;
- permit loss, worker termination, redelivery, and ambiguous commits converge to one durable effect;
- remote pressure remains bounded across transport replicas;
- object-store throttling raises permit and queue age without uncontrolled request growth;
- critical reserve and backfill ceilings remain enforced under contention;
- idle reserved catalogue shares are borrowable and return to a waiting class as grants drain;
- catalogue saturation keeps ingestion deliveries heartbeated and pending without consuming
  processing attempts, publishing failures, or failing worker health;
- maintenance cannot overlap any granted hot catalogue operation;
- poison materialization jobs reach dead letter without crash-looping ingestion; and
- metrics distinguish executor shortage from remote, catalogue, and object-store saturation.

## Lessons retained from the shared-connection and fan-out incidents

The old combined catalogue process showed that unrelated coroutines must not share one embedded
DuckDB connection or failure boundary. Ingestion and materialization therefore remain separate
deployments and own their connections.

The later materialization backlog and native DuckLake failure showed that independently scaling
compute and commit queues can amplify downstream pressure, and that maintaining a mutable fan-out
ledger duplicates completion state. The durable lessons are:

- one bounded materialization scope owns compute through coverage;
- coverage, not settlement bookkeeping, is authoritative;
- replicas provide executors but never define shared-resource capacity; and
- pressure arbitration belongs in one narrow governor rather than workflow-specific repair loops.
