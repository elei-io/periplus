# Architecture cutover audit

This file tracks the remaining gap between the implementation and Atlas's accepted worker
architecture. The canonical contracts are:

- [Architecture](docs/ARCHITECTURE.md)
- [Worker architecture](docs/WORKER_ARCHITECTURE.md)
- [Crawl graphs](docs/CRAWL_GRAPHS.md)
- [Catalogue definitions and materialization](docs/PUBLICATIONS.md)
- [Hazards](docs/HAZARDS.md)

The former combined crawl/catalogue worker audit and two-worker plan are superseded. They must not
be used as implementation guidance.

## Accepted direction

Atlas has independent runtime scaling and failure domains for:

1. HTTP page acquisition;
2. browser page acquisition;
3. optional external-provider page acquisition;
4. base evidence ingestion and graph navigation;
5. live user materialization; and
6. leased storage maintenance.

Acquisition workers publish immutable raw HTML and frozen ingestion jobs. Ingestion workers commit
base evidence, navigation-critical system projections, navigation packages, and outgoing edges.
Materialization workers drain live CDC/backfill scope work independently. Maintenance remains
off-path.

Materialization lag is not graph execution state. Stopping every materialization worker must leave
acquisition, ingestion, navigation, and graph completion operational.

## Known cutover gaps

Until the worker cutover is complete, code may still contain parts of the superseded topology. These
are migration work, not supported alternative contracts:

- a combined catalogue process may still supervise ingestion and materialization together;
- ingestion code may still consume materialization commit work;
- transport profiles may still share one crawl subject and worker image;
- Compose and process entrypoints may still name the old catalogue worker;
- health and presence may not yet be separated by ingestion and materialization deployment; and
- a process-local shared-connection lock may still protect the combined loop.

The cutover deletes each obsolete path when its replacement becomes active. Do not add aliases,
fallback consumers, dual publication, or compatibility modes.

## Reliability gates

Before production, tests must prove:

- a stopped materialization deployment causes visible per-view lag but no crawl or graph stall;
- restarting materialization catches up from durable positions without reacquisition;
- a stopped acquisition deployment does not prevent retained materialization work from draining;
- HTTP and browser acquisition capacity scale independently;
- duplicate delivery and worker termination around every commit are idempotent;
- multiple ingestion replicas safely commit distinct operations;
- multiple materialization replicas safely commit distinct scopes;
- overlapping writes retry with bounded backoff and observable conflict metrics;
- queue age, not process liveness alone, drives health and autoscaling; and
- maintenance cannot overlap an unfenced hot-path commit.

## Evidence from the shared-connection incident

The combined catalogue process allowed ingestion and materialization settlement to use one embedded
DuckDB/DuckLake connection from independently scheduled coroutines. Result state was corrupted and
materialization work starved ingestion while the process remained alive.

Serializing that connection and bounding materialization batches is the immediate safety fix. It is
not the target architecture. The durable lesson is that one execution lane owns one connection and
that ingestion and materialization need separate processes, health, queues, and scaling controls.
