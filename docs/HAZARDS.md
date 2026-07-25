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

**Putting crawl history in Postgres.** Postgres owns editable control state and current workflow
state. Crawl evidence belongs in DuckLake; current graph rows are checkpoints, not crawl history.

**Using NATS as workflow authority or analytical history.** JetStream delivers graph work from the
transactional Postgres outbox. Redelivery is expected; Postgres generation and checkpoint state
decide whether work is current. Durable evidence belongs in DuckLake and immutable objects.

**Putting graph execution state in DuckLake or NATS KV.** Runs, crawl requests, admission frontiers,
deduplication markers, claims, retries, scheduler state, and completion counters are transactional
Postgres state. DuckLake contains retained crawl evidence only; NATS only delivers work. Neither is
a workflow recovery database.

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

**Reintroducing global resource locking.** Basin owns managed-lake capacity and each Atlas process
owns bounded client and object-store pools. Do not coordinate unrelated catalogue, object, query,
and hostname work through one deployment-wide lock or KV record. Distributed concurrency belongs
only where Atlas owns the cross-replica contract, currently one independent key per website domain.

**Holding Atlas Postgres locks across remote lake work.** A lock that starts around identity
resolution and remains held while Quack performs a DuckLake transaction turns shared URL and
content identities into a writer convoy. Durable ingestion uses its operation lease and the
DuckLake transaction contract; do not wrap it in PostgreSQL advisory locks. Advisory locks are
not part of Atlas's catalogue execution model.

**Holding a Postgres connection across remote work.** Load and validate the bounded state required
for CDP, object-store, NATS, or DuckBasin work, release the session, perform the remote operation,
then open a short transaction to fence and commit the result. Row locks belong only around local
state transitions. The fixed no-overflow pool is a hard safety rail, not a reason to hold sessions
while waiting.

**Leasing only a crawl request while appending shared catalogue identities.** Different crawls can
discover the same URL or capture identical content. DuckLake append transactions and insert-only
`MERGE` statements do not enforce Atlas's logical uniqueness by themselves, so fencing only the
request identity lets concurrent batches both observe a missing canonical row and append it.
Preflight existing immutable URL, document, and artifact identities in one bounded lookup, then
lease every still-missing identity in stable order. The transaction must authoritatively re-read
those identities after acquiring the leases. Existing documents undergoing projection replacement
remain leased because their document and element rows are mutated.

**Provisioning JetStream from request or polling paths.** `add_consumer()` is a control-plane
mutation even when the durable already exists. Repeating it from API reads, scheduler ticks, or
worker loops can force consumer leader elections and make a healthy NATS cluster time out. Reconcile
streams, consumers, and KV contracts once during process startup, look up a durable before creating
it, and reuse the resulting process-owned handles for ordinary work.

## Catalogue correctness

**Sharing session-affine Basin connections.** Every DuckDB client has a routing session ID and owns
its minted Quack attachment for its lifetime. One client remains serialized, while a process gains
concurrency from its bounded client pool. Never hand one connection to concurrent operations or
reconstruct its URI without the same session ID.

**Reminting an active Basin transaction.** Before an operation starts, Atlas replaces a connection
whose OAuth credential generation is stale. It may also retry one nontransactional operation after
Quack reports an invalid session or rejected credential. Once `BEGIN` succeeds, never move work to
a replacement session: surface the failure so the owning durable job can retry the whole
transaction.

**Running interactive analytics on the API's catalogue-control connection.** Each API replica has
one small, serialized Basin connection for mandatory definition work. Interactive routes use the
separate bounded Basin client pool; they never share the definition connection.

**Giving agents a second catalogue path.** Agent tools must use the same validated interactive
Quack boundary as the workbench. Do not let a model connect directly to DuckLake, execute SQL on the
API control connection, call an internal HTTP route, or move the tool loop into the browser. The
assistant is catalogue-only and request-scoped and uses only the shared interactive boundary. Do
not give it crawl, schedule, NATS graph, live-page, web-search, acquisition, mutation, or
server-side conversation-history capabilities. A console may send a small bounded context slice
that exists only in that console session. Keep typed catalogue capabilities transport-neutral and
adapt them to PydanticAI, CLI, or MCP at the edge.

**Exposing Quack or storage configuration to the browser.** Cloudflare Access protects Atlas Web/API,
not Quack. The browser submits SQL to Atlas API and consumes Arrow IPC. Keep the Quack URI and token,
Postgres DSN, object-store credentials, and DuckLake attachment SQL server-side. Quack must remain
private and network-restricted to API compute.

**Treating read-only SQL as sufficient validation.** A `SELECT` can still read local files, invoke
dynamic SQL, inspect secrets, or cross catalogue boundaries through table functions. Interactive
queries must pass Atlas's stricter public-catalogue validator before reaching Quack.

**Assuming Quack preserves non-main schema qualification.** The current attached-table scan path
can bind `schema.table` locally but generate an unqualified server scan of `table`. This is already
reproduced against `nl.train_stations` and directly affects Atlas's `_atlas` and
`_atlas_materializations` schemas. Do not hide it behind an Atlas `query(...)` rewrite or collapse
tables into `main`. The risk is explicitly accepted for the initial managed deployment; retain
qualified SQL and consume the upstream Quack fix when released.

**Treating a long Quack mutation as exactly-once without query-completion acknowledgement.** The
pre-acknowledgement Quack build can replay a long CTAS against the same server connection: the
second execution reports that its new table already exists, then rollback leaves no committed
target. Making the statement `OR REPLACE` is not a correctness fix because the transaction itself
can disappear before Atlas observes `COMMIT`. Do not acknowledge materialization work, advance its
snapshot fence, or retry it as ordinary SQL until the deployed client and server include and enable
Quack's query-completion acknowledgement/reconnect contract.

**Treating Quack's public `query(...)` macro as a lake sandbox.** A Basin Quack server currently
lets remote SQL resolve its attached `control` Postgres catalog. Catalog enumeration exposed
control-plane relation names and metadata schemas outside the selected lake; no control-plane rows
were read during the probe. Private networking and service-account authentication reduce exposure
but are not an authorization boundary inside the server session. While this is temporarily
accepted for trusted compatibility testing, never pass user-authored SQL to the macro, keep its
credential least-privileged and server-side, and assume credential compromise can reach Basin
control data. Production isolation requires a per-lake restricted database role or a separate
internal connection that external SQL cannot resolve.

**Letting historical edge joins drift during a run.** Page-only edges use their current immutable
navigation package. Catalogue-reading edges use the snapshot pinned before the run; never silently
switch them to the latest snapshot on retry.

**Treating Basin CDC as editable control state.** Basin's JetStream subjects describe durable lake
changes; they do not distribute Atlas's editable Postgres state. The catalogue ingress republishes
table ticks and DDL changes into Atlas NATS, downstream consumers retain independent Atlas cursors,
and lifecycle intent remains explicit in Postgres.

**Consuming Basin CDC directly in downstream workers.** The catalogue ingress is the only Atlas
consumer of Basin NATS. Materializations and future sinks consume Atlas-owned subjects so one
downstream failure cannot retain the managed source cursor.

**Assuming an `atlas.>` NATS permission covers Atlas KV buckets.** JetStream KV values use
`$KV.<bucket>.>` wire subjects, so ordinary `atlas.>` publish/subscribe permissions do not cover
them. Atlas uses a dedicated NATS account whose normal namespace identity has account-wide `>`
permissions; account isolation, rather than a subject-name prefix, is the security boundary. If a
more restrictive policy is introduced, it must explicitly cover every code-owned
`$KV.<atlas_bucket>.>` subject. NATS `*` matches a whole subject token, so `$KV.atlas_*.>` is not a
valid prefix wildcard. Without the data-subject permission workers may create or attach buckets
through the management API yet fail their first value write.

**Using one acknowledgement rule for unlike inputs.** Commands are retained until their requested
durable effect and terminal state exist. Materialization ticks are retained until the derived
target commits. Basin CDC is acknowledged only after all corresponding Atlas publications receive
PubAcks. Timer-driven housekeeping has no work-message acknowledgement. Do not hide these meanings
behind a generic completion callback.

**Crossing a driving-table schema boundary in place.** A materialization
incarnation is pinned to a table UUID and schema. Block it on incompatible DDL;
dematerialize, edit, and create a new incarnation.

**Creating permanent per-crawl Parquet files.** DuckLake owns analytical layout and compaction.

**Reintroducing Atlas lake maintenance.** DuckBasin owns compaction, snapshot expiry, old-file
cleanup, and all physical lake upkeep. Atlas housekeeping may remove only Atlas-owned local staging
and expired navigation objects; it never opens DuckLake or consumes catalogue events.
