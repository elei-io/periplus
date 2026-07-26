# Atlas architecture

Atlas turns URL inputs into durable crawl evidence and derived catalogue data. A standard CDP
endpoint is the only page-acquisition boundary. Atlas does not select an HTTP client, browser
vendor, proxy, profile, or transport; the CDP service owns those decisions and their capacity.

## Ownership

| System | Owns |
|---|---|
| Postgres | Editable control state plus current graph runs, requests, edge evaluations, admission deduplication, progress counters, and the graph outbox |
| Atlas NATS JetStream/KV | Graph work delivery, leases, workers, domain pacing, query status, and catalogue events |
| Basin NATS JetStream | Managed DuckLake global DDL and DML CDC source streams |
| CDP service | Acquisition transport, provider selection, browser farm, and acquisition capacity |
| DuckBasin | DuckLake metadata, Parquet storage, Quack compute, session routing, CDC, compaction, and lake maintenance |
| Object repository | Immutable content-addressed raw HTML and bounded navigation packages |
| Managed DuckLake | Crawls, documents, DOM, graph provenance, materialized data, and snapshots |

The durable path is:

```text
URL -> crawl request -> CDP session -> immutable HTML -> navigation package -> scoped SQL edges
                                             \-> durable ingestion -> DOM/catalogue
```

## Analytical path

Atlas preserves crawl and retained-content evidence in the canonical lake, then lets users derive
their own claims, products, entities, relationships, and other analytical observations through
ordinary DuckDB-compatible SQL. Those interpretations are not universal base-table facts. Views
retain crawl, document, URL, and capture-time provenance so an aggregate answer can be inspected at
its source evidence. User-owned materializations remain optional.

The intended analytical path is:

```text
new crawl evidence
  -> compiler-scoped SQL over crawls, documents, and elements
  -> user-defined observations and relationships
  -> cross-page, cross-domain, temporal, or graph analysis
  -> auditable result or new crawl inputs
```

Interactive queries may analyze a bounded document population directly. Repeated or global
analysis can use ordinary virtual views and CTEs while retaining the same compiler boundary. Atlas
first improves execution over the canonical evidence relations. A user may choose to materialize a
derived relation, and Atlas may eventually introduce transparent physical acceleration, but neither
is required by the logical query contract.

[Vision](VISION.md) defines the product thesis.
[Analytical benchmarks](ANALYTICAL_BENCHMARKS.md) defines the ground-truth and performance contract
used to prove it.

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
historical catalogue state. Acquisition transactionally checkpoints navigation readiness into the
graph outbox after ingestion work has a durable JetStream PubAck. The relay marks delivery only
after its own PubAck, so traversal completion means every catalogue job was accepted, not that
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

Ingestion is `critical`. Each process mints four independent DuckBasin clients with expiring
service-account tokens and routing session IDs. Local Arrow or Parquet batches stream through Quack
into the remote lake; Atlas never receives the lake's object-store credentials.

The catalogue ingress owns two durable consumers in Basin NATS: one global DML tick stream and one
global DDL stream for the selected lake. It republishes DDL into Atlas NATS and resolves each DML
`table_id` to its stable DuckLake table UUID before per-table fan-out. A Basin message is ACKed only
after every Atlas publication receives a PubAck.

The catalogue-wide SQL compiler core lives under `backend/catalogue/compiler/`; every application
caller enters through the public `atlas_sql.AtlasCompiler` SDK facade. Embedded Atlas callers and
the remote `POST /catalogue/sql/compile` transport return the same typed result containing validity,
support status, executable SQL, diagnostics, rewrites, compiler version, and catalogue revision.
Interactive callers execute authored SQL when optimization is unsupported. Materialization requires
a supported proof and remains ineligible otherwise.

Each materialization owns a filtered durable Atlas NATS consumer, coalesces ticks, and
transactionally refreshes its stable table using its declared keyed, append-only, or full strategy.
User-authored SQL remains ordinary DuckDB SQL. The fail-closed
[catalogue compiler](CATALOGUE_COMPILER.md) attempts to prove a bounded plan for the selected
strategy and stable output key. When optimization is unavailable, the logical view remains valid
for interactive use but is not eligible for materialization; Atlas never requires authored SQL
hints or silently substitutes an unproved refresh plan.
Keyed and append-only incarnations create that consumer before an empty private target, service
live CDC immediately, and populate historical driving keys through bounded hash partitions. The
virtual source view remains public until every partition commits; the worker then publishes the
private target while retaining the same consumer for steady-state maintenance. Postgres persists
only the partition count and next-partition cursor, while the partial result remains in DuckLake.
DuckLake transaction conflicts, including conflicts with managed compaction, are retried without
changing that cursor. A failed incarnation may be explicitly retried from its Postgres checkpoint:
creation rediscovers its private target, backfill continues at the next uncommitted partition, and
live refresh retains its processed-snapshot fence. Schema-blocked incarnations remain ineligible
for retry because their durable table contract changed.
Keyed refreshes derive composite keys from DuckLake's native bounded
`ducklake_table_changes(...)` history. The catalogue compiler derives physical scan bindings from
ordinary SQL and stored macro lineage, then injects those keys into every proven dependent scan
before evaluating the materialization SQL. Materialization workers never consume Basin CDC
directly.

DuckBasin owns file layout, compaction, snapshot expiry, and old-file cleanup. Atlas has no lake
maintenance API or worker. Atlas housekeeping is limited to its own abandoned ingestion staging
files and expired navigation packages. Historical graph edges mint bounded, read-only Basin
connections and are serialized within each acquisition process.

Interactive catalogue reads enter through the Atlas API. Each API replica owns a bounded pool of
reusable DuckDB Quack client connections; analytical CPU, memory, and DuckLake access remain on the
private Quack service. The API validates one read-only statement, rejects external file, dynamic
SQL, secret, and non-Atlas catalogue access, borrows one client from its local pool, and streams
bounded Arrow IPC results. Query status and cancellation requests use the expiring
`atlas_catalogue_queries` NATS KV bucket, so another API replica can observe or cancel an execution.
Timeout, row, encoded-byte, and local-pool limits are mandatory. Basin owns remote admission and
compute scaling.

Interactive scans of the managed `elements` table have an additional compiler-enforced boundary.
Ordinary SQL may constrain `document_id` directly or derive it through canonical managed-table
relationships. For a derived scope, one session-affine Quack client opens a snapshot transaction
that performs no lake mutation,
resolves at most 10,000 distinct documents, verifies that their declared element total is at most
50,000,000, hydrates one temporary projected `elements` relation, and evaluates the compiled query
against it before committing. Every stage observes the same DuckLake snapshot. If the compiler
cannot prove the document lineage, Atlas rejects the query as an unbounded DOM scan rather than
executing it unchanged.

Atlas AI is an API-owned adapter over that same interactive query boundary. Its single PydanticAI
loop and typed catalogue tools live under `backend/agents/`; neither the model nor a console
receives a Quack connection or storage credentials. Each request contains one prompt and an
optional bounded list of recent console-session messages. The API does not load or persist chat
history. The agent can inspect public relations and macros and execute validated, bounded,
read-only SQL. Relation inspection includes the transactional DuckLake comments attached to tables,
views, and columns. A non-executing lint tool exposes the authoritative interactive compiler result,
and every executed agent query returns that same compilation decision and its diagnostics alongside
the bounded rows. A separate suggestion tool registers up to three read-only SQL handoffs only when
the compiler reports no errors or warnings. The agent emits tool progress and finishes with a
concise message plus the accepted suggestions. The console renders only their titles and
descriptions, retains them in process memory, and acts on one with `.ai N`: execution is the
default, while `--copy` copies its SQL and `--show` reveals it without execution.

The agent cannot read graph execution state, crawl or schedule control state, inspect live pages,
search the public web, propose acquisition, or mutate Atlas. The expiring NATS query-status record
remains part of the shared interactive-query boundary; it contains operational status rather than
prompt, response, or result history.
The tool implementations remain independent of HTTP and agent transport so another adapter can reuse
catalogue validation without gaining a second catalogue path.

Each API process owns one session-affine Basin catalogue-control connection, pinned to one thread
and one operation at a time. It performs definition work such as creating or updating views,
macros, and materializations. This control session remains separate from the API's bounded
interactive Basin client pool.

The browser uses ordinary authenticated HTTP to Atlas API for workbench queries, cancellation,
metadata, and status. It does not load DuckDB-Wasm and never receives the Quack URI or token,
Postgres DSN, object-store credentials, or DuckLake attachment SQL. Cloudflare Access is the
single-tenant user authentication boundary. DuckBasin remains private and authenticates Atlas
through a dedicated service account. The minter discovers the selected `DUCKBASIN_LAKE`, refreshes
OAuth credentials before expiry, gives every DuckDB client a session ID, and attaches the
corresponding horizontally routed Quack endpoint.

## Repository boundary

### Deployment-to-lake binding

One Atlas deployment is bound, for its process lifetime, to exactly one
DuckLake selected by `DUCKBASIN_LAKE`. The API, workers, and catalogue relay
must all use that same value. Changing lakes means recreating the deployment;
Atlas does not support runtime lake switching, dual reads or writes, aliases, or
migration bridges.

Test, staging, and production deployments should use separate control state and
Atlas JetStream state as well as separate lakes. The API validates the selected
lake against Atlas's canonical catalogue schema before becoming ready, so a
missing, stale, or otherwise incompatible schema fails deployment startup.

Raw HTML is immutable and content addressed. Object keys are repository-relative. Postgres stores
current workflow state, never crawl evidence; NATS is a delivery fabric, never workflow authority or
analytical history; DuckLake never becomes the editable control plane. Each run persists its own
deadline, defaulting from `ATLAS_GRAPH_MAX_RUN_SECONDS`, and may be paused and resumed without losing
its frontier. Housekeeping deletes terminal operational rows in bounded batches after
`ATLAS_GRAPH_RUN_RETENTION_SECONDS`. DuckLake remains the only long-lived crawl history.

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
freezes the schedule's current root URLs and per-run maximum crawl budget, and creates transactional
Postgres execution state plus outbox rows. A deterministic
run ID derived from the schedule and occurrence makes concurrent API replicas and crash recovery
converge on one graph run. Manual “run now” actions do not consume the schedule run count.
