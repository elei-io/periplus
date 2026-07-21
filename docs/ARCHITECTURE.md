# Atlas architecture

Atlas turns URL inputs into durable crawl evidence and derived catalogue data. A standard CDP
endpoint is the only page-acquisition boundary. Atlas does not select an HTTP client, browser
vendor, proxy, profile, or transport; the CDP service owns those decisions and their capacity.

## Ownership

| System | Owns |
|---|---|
| Postgres | Editable graphs, domain politeness, content policies, schedules, matches, schemas, macro and materialization definitions, and durable chat workspace items |
| NATS JetStream/KV | Graph runs, crawl work, progress, leases, workers, admission, and expiring resource grants |
| CDP service | Acquisition transport, provider selection, browser farm, and acquisition capacity |
| Quack service | Interactive analytical DuckDB execution and memory |
| Object repository | Immutable content-addressed raw HTML and bounded navigation packages |
| DuckLake | Crawls, documents, DOM, graph provenance, materialized data, and snapshots |

The durable path is:

```text
URL -> crawl request -> CDP session -> immutable HTML -> navigation package -> scoped SQL edges
                                             \-> durable ingestion -> DOM/catalogue
```

## Acquisition boundary

Every admitted URL resolves and freezes a `DomainPolicy` and `CrawlPolicy`. Domain policy owns
website politeness: maximum concurrency and minimum request interval. A NATS-backed hostname
health record adds deployment-wide adaptive cooldown after 429 and repeated 5xx responses, while
successful HTTP responses decay that penalty. Crawl policy owns accepted
content types, response outcomes, and four content-completion methods: dynamic waiting, fixed
waiting, scrolling, and expansion. Playwright remains an implementation detail. With every method
disabled Atlas emits only the navigation and content calls supported by a static HTTP capture. The
acquisition worker applies those policies through the configured CDP endpoint, captures immutable
HTML or an accepted raw artifact, publishes frozen ingestion work, and derives the bounded
navigation package only when the node has outgoing edges.

A crawl succeeds only when Atlas captures and retains the page HTML. The CDP service may internally
satisfy a request with plain HTTP, a local browser, a remote browser, or a future strategy without
changing the Atlas contract.

The acquisition handler never opens DuckLake or waits for catalogue processing. The co-located
graph-navigation runtime opens a bounded read-only connection only for edges that explicitly join
historical catalogue state. Acquisition publishes navigation readiness after ingestion work has a
durable JetStream PubAck, so traversal completion means every catalogue job was accepted, not that
DuckLake has committed it. Atlas has one crawl
subject and one horizontally scalable acquisition-worker deployment. The CDP service, not Atlas, is
responsible for transport-specific queues and browser capacity.

## Navigation and ingestion

Acquisition-owned navigation evaluates outgoing graph edges and settles the crawl request. An edge
must use `$crawl_id` and returns URL values for its target node. `edge.page_links` is the current
page's occurrence-level Arrow package. It preserves raw hrefs, fragments, DOM order, anchor
provenance, normalized source and target URL components, and their most-specific relationship while
exposing a fragment-free crawl destination. Link text and presentation attributes are not part of
the navigation contract. It uses a standalone memory-limited DuckDB connection.
When the run acquisition window defers an evaluated edge, Atlas retains its bounded selected-URL
package under the run's navigation prefix. Redelivery resumes admission from that result instead
of rereading and requerying the source navigation package.
The seeded `views.page_links` definition reproduces those meanings from durable crawl and DOM
evidence and is declared under `fixtures/materialized_views/crawl/`. Its full
bootstrap and keyed `crawl_id` refreshes are ordinary materialization-worker
work driven by crawl-table ticks;
ingestion has no link-specific projection. Edges may also
join read-only catalogue tables; those reads use the DuckLake snapshot pinned before the graph run
starts, so retries cannot observe ingestion arriving midway through the run. Graph traversal never
waits for ingestion or materialization refreshes; historical edges intentionally tolerate recent
catalogue omissions in exchange for a stable pre-run snapshot.

Independently, an ingestion worker verifies retained HTML and commits the crawl and base DOM
evidence. It atomically commits typed `crawl_attempts` and each public `crawl_steps` method execution with its
frozen knobs, duration, stopping reason, and bounded before/after content metrics. Its queue,
terminal results, and dead letters expose catalogue lag and failures without changing a completed
graph traversal. Ingestion does not score content quality. Periodic DuckLake analysis derives
quality findings from immutable crawls, step evidence, and element rows.

## Catalogue work

Ingestion is `critical`. The catalogue relay is the only owner of DuckLake CDC
consumers: one catalogue-wide DML tick cursor and one catalogue-wide DDL
cursor publish physical events to JetStream. Each materialization owns a filtered
durable NATS consumer, coalesces ticks, and transactionally refreshes its stable
table using its declared keyed, append-only, or full strategy. Keyed refreshes
derive composite keys from a stateless bounded CDC range query; they do not open
another persistent CDC consumer. Maintenance runs off-path in bounded table slices with proportional
catalogue and object-store permits and consumes the relay's wildcard DML subject only as a coalesced
wake-up hint. DuckLake transaction conflicts are retried, so compaction can progress during
continuously active foreground work without deployment-wide quiescence. Each ingestion or
materialization process owns one embedded DuckDB connection and initially executes one catalogue
operation at a time; the
relay owns exactly its DML and DDL connections. Historical graph edges open
bounded, read-only, per-operation connections and
are serialized within each acquisition process.

Interactive catalogue reads enter through the Atlas API. Each API replica owns a bounded pool of
reusable DuckDB Quack client connections; analytical CPU, memory, and DuckLake access remain on the
private Quack service. The API validates one read-only statement, rejects external file, dynamic
SQL, secret, and non-Atlas catalogue access, acquires a deployment-wide `quack:query` permit, and
streams bounded Arrow IPC results. Query status and cancellation requests use the expiring
`atlas_catalogue_queries` NATS KV bucket, so another API replica can observe or cancel an execution.
Timeout, row, encoded-byte, local-pool, and global-concurrency limits are mandatory.

The Atlas chat agent is an API-owned adapter over that same interactive query boundary. Its
PydanticAI loop and typed catalogue tools live under `backend/agents/`; neither the model nor the
browser receives a Quack connection or storage credentials. Postgres retains Atlas-owned chat
messages, bounded normalized tool artifacts, and explicit user actions; it never stores raw provider
responses or graph execution state. The agent reads current graph-run state directly from NATS and
live graph and schedule definitions from Postgres. A turn may retain several independently
approvable acquisition plans and schedule-change proposals. The sole bounded acquisition
capability is a one-page reconnaissance probe: it submits the seeded `single-page` graph through
the ordinary run path and observes that crawl's NATS ingestion result until its base evidence is
catalogue-visible or the bounded wait expires. Corpus execution and schedule mutation remain
explicit user actions. Each agent turn remains request-attached while Atlas emits its
own stable SSE progress events, executed SQL, bounded rows, completeness metadata, and final response. The
tool implementations are independent of HTTP and agent transport so a CLI or future MCP server can
reuse them without duplicating catalogue access or validation.

Each API process owns exactly one lightweight embedded DuckDB catalogue-control connection, pinned
to one thread and one operation at a time. It performs only mandatory definition work such as
creating or updating views, macros, and materializations, plus pinning the snapshot required by a
historical graph edge. This control connection remains separate from the API's Quack client pool;
interactive reads never execute against its embedded local catalogue.

The browser uses ordinary authenticated HTTP to Atlas API for workbench queries, cancellation,
metadata, and status. It does not load DuckDB-Wasm and never receives the Quack URI or token,
Postgres DSN, object-store credentials, or DuckLake attachment SQL. Cloudflare Access is the
single-tenant user authentication boundary. Quack remains private, authenticates the API with the
dedicated Atlas token, and is restricted at the network boundary to API compute. Because attached
catalogues are Quack-server-global, every Atlas deployment sharing one Quack service must configure
a distinct `ATLAS_CATALOGUE_ALIAS`; `USE` remains Quack-client-session-local.

## Repository boundary

Raw HTML is immutable and content addressed. Object keys are repository-relative. Postgres never
stores crawl history, NATS never becomes analytical history, and DuckLake never becomes the editable
control plane. NATS execution records are deliberately bounded operational state. A graph run may
remain active for at most `ATLAS_GRAPH_MAX_RUN_SECONDS` (seven days by default); crawl-request,
edge-evaluation, sharded admission-deduplication, compact run-summary, and progress records expire
after thirty days by default. DuckLake remains the only long-lived crawl history.

Root feeding and edge traversal admit only a bounded window of acquisition work per run. A durable
root cursor refills that window after slot release and worker restart. The shared crawl consumer
round-robins visible work by graph run and then hostname, while the deployment-wide hostname permit
and pacing key remain the final politeness gate across every run and acquisition replica. A lone run
may use all ready worker lanes; a broad run cannot publish an unbounded FIFO backlog ahead of a small
run.

## Scheduled graph runs

Crawl schedules are editable Postgres control state. A schedule selects an interval or
timezone-aware cron expression, an optional active window and maximum run count, root URLs, and
fixed overlap and misfire behavior. The API process runs the thin scheduler adapter; occurrence
admission and deterministic identity live under `runtime/`.

Every admitted occurrence uses the ordinary graph-run path: Atlas snapshots the latest saved graph,
freezes the schedule's current root URLs and per-run maximum crawl budget, and creates NATS-owned execution state. A deterministic
run ID derived from the schedule and occurrence makes concurrent API replicas and crash recovery
converge on one graph run. Manual “run now” actions do not consume the schedule run count.
