# Crawl graphs

A crawl graph is a versioned directed graph. Nodes admit URLs into the single `crawl` primitive;
scoped SQL edges derive later URL inputs from durable crawl evidence.

## Execution

1. Snapshot the editable graph into a graph run.
2. Normalize and deduplicate each admitted HTTP(S) URL.
3. Resolve and freeze its `DomainPolicy` and `CrawlPolicy`.
4. Publish one `CrawlWork` message to `atlas.graph.crawl`.
5. An acquisition worker connects to the configured standard CDP endpoint.
6. Atlas enforces domain politeness, navigates, applies enabled content-completion moves, captures
   HTML or an accepted artifact, and retains it immutably.
7. Atlas durably publishes frozen ingestion work. For a branch node, acquisition then builds and
   stores the bounded navigation package; leaf nodes skip that work.
8. Acquisition publishes readiness. Each outgoing edge runs against the current page package and
   may join the read-only catalogue snapshot pinned before the run.
9. The request settles after its edges settle; the run settles after all requests settle. Catalogue
   ingestion proceeds independently and does not hold the run open.

Every graph run freezes a positive `max_crawls` budget. Initial roots must fit inside that budget.
Graph-wide admission atomically stops accepting newly discovered URLs when the request count reaches
the budget; already admitted requests finish normally. The run records whether traversal actually
reached the limit.

There are no action-specific traversal loops, transport routes, provider profiles, shadow trials,
or direct-HTTP fallback in Atlas.

## Seeded graph library

Source-controlled definitions under `fixtures/crawl_graphs/` provide immutable single-page,
same-origin, same-site, cross-site, and all-links graphs at depths one through three. The library
also includes cross-site-then-same-site, same-site-then-cross-site, and recursive same-site query
traversal. Setup validates and seeds those complete node, edge, SQL, and deduplication definitions.
User-created graphs remain editable and appear beside the seeded library.
Depth is the number of link transitions beyond each supplied root. Cross-site scope is evaluated
relative to the current source page at every hop.

The agent's live-page reconnaissance capability uses the seeded `single-page` graph with a crawl
budget of one. It does not introduce another acquisition path. The API observes the exact crawl's
terminal ingestion state in NATS; neither graph traversal nor the acquisition worker waits for
catalogue ingestion.

### Historical consistency contract

Historical edge SQL reads only the DuckLake snapshot pinned before the graph run. Atlas never waits
for ingestion commits or materialization refreshes before starting or settling graph
traversal. That snapshot may therefore omit recently retained crawls and materialized rows even
though their durable work has already been accepted. This bounded staleness is intentional:
current-page navigation remains available from retained HTML, historical edge SQL must tolerate
incomplete recent catalogue state, and every retry within the run observes the same snapshot.

## Content policies

Domain policies match hosts and independently control maximum concurrency and minimum request
interval. Crawl policies match scheme, host, and exact or prefix path. More-specific rules win. A
required `default` policy matches every HTTP(S) URL and enables maximum-correctness behavior:

```json
{
  "accepted_content_types": ["text/html", "application/xhtml+xml"],
  "completion": {
    "wait_dynamic": {"enabled": true},
    "wait_fixed": {"enabled": false, "duration_ms": 0},
    "scroll": {"enabled": true},
    "expand": {"enabled": true}
  }
}
```

Deployment setup seeds this policy when absent. Its content settings remain visible and editable,
but its catch-all matcher and enabled state are fixed and the policy cannot be deleted. Users add
more-specific policies as overlays; the most-specific matching policy wins.

Users tune or disable completion methods for matched pages only when historical evidence shows the
work is unnecessary. With all four methods disabled Atlas emits no browser-only completion or
script-execution commands, allowing the CDP service to retain an HTTP-only strategy. Atlas owns the
method implementations, budgets, stopping rules, HTTP outcomes, retries, and website politeness.
The CDP service decides how the page is transported and how browser-fleet capacity is admitted.

## Durable contracts

- `CrawlRequest`: current NATS-owned request state and frozen policy snapshot.
- `CrawlWork`: one delivery of one page request.
- `NavigationReadinessWork`: acquired crawl plus optional acquisition-owned navigation package.
- `EdgeWork`: one bounded SQL evaluation for one source crawl and edge, with a pinned catalogue
  snapshot only when the SQL reads historical state.
- `CrawlRecord`: immutable DuckLake observation pointing at content and requested/final URL identities.
- `crawl_attempts`: typed, ordered network-attempt evidence committed atomically with its crawl.
- `crawl_steps`: public per-method duration, configuration, stopping, and content-change evidence
  committed atomically with its crawl.

Acquisition failures may be retried by redelivery. Retry evidence is retained with the final logical
crawl so successful recovery does not erase earlier 429 or navigation observations. Invalid work is terminated. A successful worker
ACK requires retained HTML, durable ingestion publication, and durable navigation-readiness
publication. A run cancellation settles every nonterminal request. Ingestion terminal state and
dead letters remain independently observable and never retroactively rewrite traversal status.
HTTP 429 responses immediately apply a shared hostname cooldown using `Retry-After` when present;
repeated 5xx responses apply a weaker capped cooldown. The state is shared across graph runs and
acquisition replicas, and cooled hostnames remain buffered before domain permits and local lanes.
Every run has one deployment-wide wall-clock ceiling, `ATLAS_GRAPH_MAX_RUN_SECONDS`, which defaults
to seven days. Expiry stops admission and settles every nonterminal request. Execution state is
operational rather than historical: request, edge-evaluation, and sharded admission-deduplication
detail, compact run summaries, and progress have a thirty-day default retention window. The durable
crawl and DOM evidence remains in DuckLake after those NATS records expire.
Terminal request settlement also updates a bounded per-run failure summary in NATS, grouped by
stage, failure code, and HTTP status with one recent example. Operational metrics read that compact
summary directly; they do not scan crawl requests or join catalogue ingestion state on demand.

## Scaling

Scale Atlas acquisition-worker replicas for coordination throughput. Domain policies limit website
pressure independently. Scale the CDP service and its browser farm for transport capacity. Atlas
continues to govern its own object-store, ingestion, DuckLake, and materialization pressure.
Initial roots and edge traversal share a bounded per-run acquisition window instead of publishing
their complete frontier. A durable root cursor refills available slots across worker restarts.
Mixed-host runs interleave initial roots and each bounded edge result by normalized
hostname. Acquisition workers retain a bounded run- and hostname-aware delivery window and assign local execution
lanes only after the distributed domain permit is available, preventing one saturated hostname's
politeness waiters from blocking ready work for another hostname.
If that run window defers admission after edge SQL has selected URLs, the selected result is retained
with the run's navigation objects and reused across worker redelivery and restart.

## Schedules

Schedules belong to a graph and use either a fixed interval or a cron expression with an IANA
timezone. Optional `starts_at` and exclusive `ends_at` values bound occurrence eligibility. An
optional maximum counts durably created graph runs, whether those runs later succeed or fail.

Interval schedules are anchored to `starts_at` when supplied. Without a start, the first occurrence
is one interval after creation. Cron schedules calculate occurrences in their configured timezone.
Each occurrence freezes the latest saved graph and the schedule's current normalized root URL list.

The default overlap policy skips an occurrence while a prior run from that schedule remains active.
The default misfire policy skips occurrences older than the configured grace window; `run_once`
instead creates one catch-up run and advances directly to the next future occurrence. Atlas never
replays an unbounded missed-run backlog.
