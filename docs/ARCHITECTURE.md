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
must use `$crawl_id` and returns URL values for its target node. `page.links` is the current page's
Arrow package and uses a standalone memory-limited DuckDB connection. Edges may also join read-only
catalogue tables; those reads use the DuckLake snapshot pinned before the graph run starts, so
retries cannot observe ingestion arriving midway through the run.

Independently, an ingestion worker verifies retained HTML and commits the crawl and base DOM
evidence. It atomically commits each method execution to private `_atlas.crawl_steps` with its
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

Interactive catalogue reads do not execute in the API process. The browser loads DuckDB-Wasm,
attaches the configured Quack endpoint, and drives a server-side session with Atlas's DuckLake
attached. Only the SQL workbench uses this browser runtime: its queries, completion metadata, and
catalogue status. Crawl failures, run progress, and worker backlogs come from NATS.

Each API process owns exactly one lightweight embedded DuckDB catalogue-control connection, pinned
to one thread and one operation at a time. It performs only mandatory definition work such as
creating or updating views, macros, and materializations, plus pinning the snapshot required by a
historical graph edge. It never runs analytical UI reads, never polls DuckLake, has no read pool,
and has no query-proxy fallback. The API validates workbench SQL and supplies typed Quack/DuckLake
bootstrap configuration, but the browser sends the query directly to Quack. Quack is therefore
required for the web application.
Because attached catalogues are Quack-server-global, every Atlas deployment sharing one Quack
service must configure a distinct `ATLAS_CATALOGUE_ALIAS`; `USE` remains browser-session-local.

## Repository boundary

Raw HTML is immutable and content addressed. Object keys are repository-relative. Postgres never
stores crawl history, NATS never becomes analytical history, and DuckLake never becomes the editable
control plane.

## Scheduled graph runs

Crawl schedules are editable Postgres control state. A schedule selects an interval or
timezone-aware cron expression, an optional active window and maximum run count, root URLs, and
fixed overlap and misfire behavior. The API process runs the thin scheduler adapter; occurrence
admission and deterministic identity live under `runtime/`.

Every admitted occurrence uses the ordinary graph-run path: Atlas snapshots the latest saved graph,
freezes the schedule's current root URLs, and creates NATS-owned execution state. A deterministic
run ID derived from the schedule and occurrence makes concurrent API replicas and crash recovery
converge on one graph run. Manual “run now” actions do not consume the schedule run count.
