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

**Letting acquisition wait for ingestion.** An acquisition worker retains HTML, durably publishes
ingestion, derives branch navigation, and ACKs after readiness publication. The crawl handler never
opens DuckLake or waits for catalogue completion; only an explicit historical edge join may open a
bounded read-only catalogue connection.

**Putting graph traversal behind catalogue ingestion.** Acquisition owns navigation readiness and
outgoing scoped SQL edges from the current page package. Ingestion evolves the catalogue
independently and must not settle or fail graph traversal.

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

**Running interactive analytics in the API's embedded catalogue.** Each API replica has one small,
serialized DuckDB connection for mandatory catalogue definition work. Interactive routes must use
the separate bounded Quack client pool; they must never execute analytical reads locally or share
the definition connection.

**Giving agents a second catalogue path.** Agent tools must use the same validated interactive
Quack boundary as the workbench. Do not let a model connect directly to DuckLake, execute SQL on the
API control connection, call an internal HTTP route, or move the tool loop into the browser. Keep
typed capabilities transport-neutral and adapt them to PydanticAI, CLI, or MCP at the edge.

**Exposing Quack or storage configuration to the browser.** Cloudflare Access protects Atlas Web/API,
not Quack. The browser submits SQL to Atlas API and consumes Arrow IPC. Keep the Quack URI and token,
Postgres DSN, object-store credentials, and DuckLake attachment SQL server-side. Quack must remain
private and network-restricted to API compute.

**Treating read-only SQL as sufficient validation.** A `SELECT` can still read local files, invoke
dynamic SQL, inspect secrets, or cross catalogue boundaries through table functions. Interactive
queries must pass Atlas's stricter public-catalogue validator before reaching Quack.

**Letting historical edge joins drift during a run.** Page-only edges use their current immutable
navigation package. Catalogue-reading edges use the snapshot pinned before the run; never silently
switch them to the latest snapshot on retry.

**Treating CDC locks as control-plane messaging.** DuckLake CDC cursors describe
durable data ordering; they do not distribute editable Postgres state. Publish
table ticks and DDL changes through the catalogue relay, retain downstream NATS
cursors, and make lifecycle intent explicit in Postgres.

**Opening DuckLake CDC consumers in downstream workers.** The catalogue relay
is the sole DuckLake CDC owner. Materializations, maintenance, publications,
and future sinks consume its JetStream subjects so one downstream failure
cannot retain or advance the shared source cursor.

**Using one acknowledgement rule for unlike inputs.** Commands are retained
until their requested durable effect and terminal state exist. Materialization
ticks are retained until the derived target commits. Maintenance ticks are
wake-up hints and are acknowledged after the wake is recorded, with the
periodic metadata sweep as recovery. Do not hide these meanings behind a
generic completion callback.

**Crossing a driving-table schema boundary in place.** A materialization
incarnation is pinned to a table UUID and schema. Block it on incompatible DDL;
dematerialize, edit, and create a new incarnation.

**Creating permanent per-crawl Parquet files.** DuckLake owns analytical layout and compaction.

**Running maintenance in the foreground.** Maintenance is off-path and requires an exclusive background
catalogue permit.
