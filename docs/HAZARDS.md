# Architecture hazards

Read this before adding a service, queue, persistence path, compatibility layer, or abstraction.

## Acquisition ownership

**Adding another acquisition provider to Atlas.** Standard CDP is the sole acquisition boundary.
Provider selection, direct HTTP, browser vendors, proxy/profile concerns, browser farms, and
transport cost optimization belong behind that endpoint. Atlas has one CDP URL and one crawl queue.

**Adding provider-specific side channels.** The only acquisition contract is standard CDP. The
retained result is the HTML Atlas captures after its content policy completes.

**Moving content completion into transport policy.** Rendering capability belongs to the CDP
service; deciding whether captured content is complete belongs to Atlas. Keep dynamic waiting,
fixed waiting, scrolling, and expansion in `CrawlPolicy` and freeze them at admission.

**Moving website politeness into the CDP service.** The CDP service owns browser-fleet capacity, not
per-domain crawl behavior. Atlas must enforce `DomainPolicy` concurrency and pacing regardless of
which standard CDP endpoint is configured.

## State ownership

**Putting crawl history in Postgres.** Postgres is editable control state. Crawl evidence belongs in
DuckLake; current work and progress belong in NATS.

**Using NATS as analytical history.** JetStream/KV state is current, bounded, and repairable. Durable
evidence belongs in DuckLake and immutable objects.

**Scoring content quality during ingestion.** Ingestion produces faithful structural evidence.
Quality hypotheses change independently and must run later over DuckLake crawl attempts and element
rows; changing an analyzer must never invalidate or rebuild the DOM projection.

**Writing raw objects outside the repository.** HTML must be immutable, content addressed, and stored
through `backend/repository/` with repository-relative keys.

**Adding compatibility paths in a greenfield system.** Change the contract and delete its predecessor.
Do not add aliases, dual reads/writes, fallback routes, deprecated environment variables, or migration
bridges for disposable development state.

## Work ownership

**Letting acquisition wait for ingestion.** An acquisition worker retains HTML, durably publishes ingestion,
and ACKs. It never opens DuckLake or waits for downstream completion.

**Evaluating graph edges in acquisition.** Ingestion owns navigation readiness and outgoing scoped SQL
edges. Acquisition handles exactly one page.

**Creating action-specific traversal loops.** `crawl` is the only acquisition primitive. Express
navigation with nodes and scoped SQL edges.

**Using a generic completion or lock workflow.** Workers retain their durable work message while
waiting for atomic expiring permit bundles. Permits, operation leases, and advisory locks have
separate pressure, duplication, and correctness roles.

**Letting Resource Governor own workflow.** It is an admission controller, not a queue, completion
ledger, materialization orchestrator, or catalogue RPC service.

## Catalogue correctness

**Sharing embedded DuckDB connections.** Each ingestion or materialization process owns its connection
and initially runs one catalogue operation at a time. Scale with replicas under shared permits.

**Publishing unbounded materialization work.** Discovery emits deterministic bounded scopes. One worker
commits one authoritative scope; do not add a fan-out ledger or settlement queue.

**Creating permanent per-crawl Parquet files.** DuckLake owns analytical layout and compaction.

**Running maintenance in the foreground.** Maintenance is off-path and requires an exclusive background
catalogue permit.
