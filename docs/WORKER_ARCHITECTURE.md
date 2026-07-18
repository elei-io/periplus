# Worker architecture

Atlas deploys four worker roles:

| Worker | Input | Output | Scaling dimension |
|---|---|---|---|
| Acquisition | `atlas.graph.crawl` | Immutable HTML and ingestion job | CDP coordination |
| Ingestion | repository ingestion queue | Crawl/DOM evidence and graph readiness | Critical catalogue capacity |
| Materialization | live/backfill scope queues | Authoritative materialized scopes | Live/backfill catalogue capacity |
| Maintenance | maintenance triggers | Compaction and cleanup | Exclusive maintenance capacity |

## Acquisition worker

Each delivery represents one page. The worker claims the request, obtains Atlas object-write
capacity, obtains the URL's domain concurrency permit, observes its minimum request interval,
connects to the configured CDP endpoint, navigates, applies the enabled content-completion moves,
captures final HTML or an accepted artifact, stores it, and publishes ingestion work.
Each worker process owns one Playwright driver, while every delivery opens and closes its own CDP
connection and page so crawl state is not shared between deliveries.
When every method is disabled it sends no browser-only script or page-completion commands and
performs no page evaluation or interaction. It then ACKs. It never opens DuckLake, evaluates edges,
or chooses a provider.

There is one acquisition deployment and one durable crawl consumer. The CDP service owns the browser
farm, transport selection, proxy/profile concerns, and external scaling. Atlas retains website
politeness, response handling, retry evidence, and content correctness. It must not recreate
provider-specific queues, browser slots, direct HTTP clients, or transport fallback logic.

## Ingestion worker

The ingestion worker verifies immutable HTML, prepares DOM and navigation data, commits base
evidence and private `_atlas.crawl_steps` rows under the catalogue fence, publishes readiness, and
ACKs. It does not calculate quality flags; periodic analysis derives quality findings from committed
step and element rows. It is critical work. One process owns one embedded DuckDB connection and
runs one catalogue operation at a time.

## Materialization and maintenance

Materialization discovery directly publishes deterministic bounded scopes. One worker evaluates and
commits one scope through authoritative coverage. Maintenance is off-path and requires an exclusive
background catalogue permit.

Permits control shared pressure, operation leases suppress duplicate execution, and PostgreSQL
advisory locks fence commits. None of these mechanisms owns work delivery or workflow completion.

## Packaging and deployment

All roles use the same backend image and the same `atlas-worker <role>` entrypoint. Acquisition,
ingestion, materialization, and maintenance remain separate deployments so each retains independent
autoscaling, rollout, health, queue, and failure boundaries. Sharing packaging must not couple their
replica counts or cause one workload's backlog to add capacity to another workload.

Local Compose passes only addresses and credentials that differ inside its container network, plus
the small set of documented deployment safety rails. Runtime mechanics use validated code defaults;
they are not repeated as Compose interpolation knobs. Shared YAML anchors keep the remaining
catalogue, repository, NATS, and control-plane contracts consistent across roles.
