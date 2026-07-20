# Atlas architecture

Atlas turns URL inputs into durable crawl evidence and derived catalogue data. A standard CDP
endpoint is the only page-acquisition boundary. Atlas does not select an HTTP client, browser
vendor, proxy, profile, or transport; the CDP service owns those decisions and their capacity.

## Ownership

| System | Owns |
|---|---|
| Postgres | Editable graphs, domain politeness, content policies, schedules, matches, schemas, macro definitions, and materialization definitions |
| NATS JetStream/KV | Graph runs, crawl work, progress, leases, workers, admission, and expiring resource grants |
| CDP service | Acquisition transport, provider selection, browser farm, and acquisition capacity |
| Quack service | Interactive analytical DuckDB execution and memory |
| Object repository | Immutable content-addressed raw HTML and bounded navigation packages |
| DuckLake | Crawls, documents, DOM, graph provenance, materialized data, snapshots, and coverage |

The durable path is:

```text
URL -> crawl request -> CDP session -> immutable HTML -> navigation package -> scoped SQL edges
                                             \-> durable ingestion -> DOM/catalogue
```

## Acquisition boundary

Every admitted URL resolves and freezes a `DomainPolicy` and `CrawlPolicy`. Domain policy owns
website politeness: maximum concurrency and minimum request interval. Crawl policy owns accepted
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
The seeded `views.page_links` definition reproduces those meanings from durable crawl and DOM
evidence and is declared under `fixtures/materialized_views/crawl/`. Its live and backfill writes
are ordinary materialization-worker work; ingestion has no link-specific projection. Edges may also
join read-only catalogue tables; those reads use the DuckLake snapshot pinned before the graph run
starts, so retries cannot observe ingestion arriving midway through the run. Graph traversal never
waits for ingestion or materialization coverage; historical edges intentionally tolerate recent
catalogue omissions in exchange for a stable pre-run snapshot.

Independently, an ingestion worker verifies retained HTML and commits the crawl and base DOM
evidence. It atomically commits typed `crawl_attempts` and each public `crawl_steps` method execution with its
frozen knobs, duration, stopping reason, and bounded before/after content metrics. Its queue,
terminal results, and dead letters expose catalogue lag and failures without changing a completed
graph traversal. Ingestion does not score content quality. Periodic DuckLake analysis derives
quality findings from immutable crawls, step evidence, and element rows.

## Catalogue work

Ingestion is `critical`. Live and backfill materializations publish bounded authoritative scopes.
Maintenance runs off-path with an exclusive background catalogue permit. Each ingestion or
materialization process owns one embedded DuckDB connection and initially executes one catalogue
operation at a time. Historical graph edges open bounded, read-only, per-operation connections and
are serialized within each acquisition process.

Interactive catalogue reads enter through the Atlas API. Each API replica owns a bounded pool of
reusable DuckDB Quack client connections; analytical CPU, memory, and DuckLake access remain on the
private Quack service. The API validates one read-only statement, rejects external file, dynamic
SQL, secret, and non-Atlas catalogue access, acquires a deployment-wide `quack:query` permit, and
streams bounded Arrow IPC results. Query status and cancellation requests use the expiring
`atlas_catalogue_queries` NATS KV bucket, so another API replica can observe or cancel an execution.
Timeout, row, encoded-byte, local-pool, and global-concurrency limits are mandatory.

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
control plane. NATS execution records are deliberately bounded operational state: crawl-request and
edge-evaluation records expire after seven days by default, while compact graph-run summaries and
progress expire after thirty days. Terminal runs discard their admission deduplication set once all
requests settle. DuckLake remains the only long-lived crawl history.

## Scheduled graph runs

Crawl schedules are editable Postgres control state. A schedule selects an interval or
timezone-aware cron expression, an optional active window and maximum run count, root URLs, and
fixed overlap and misfire behavior. The API process runs the thin scheduler adapter; occurrence
admission and deterministic identity live under `runtime/`.

Every admitted occurrence uses the ordinary graph-run path: Atlas snapshots the latest saved graph,
freezes the schedule's current root URLs, and creates NATS-owned execution state. A deterministic
run ID derived from the schedule and occurrence makes concurrent API replicas and crash recovery
converge on one graph run. Manual “run now” actions do not consume the schedule run count.
