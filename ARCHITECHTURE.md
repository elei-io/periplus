# Atlas Architecture

This document describes the agreed target architecture. The detailed storage contract lives in
[`STORAGE.md`](STORAGE.md), and the operational metric contract lives in
[`METRICS.md`](METRICS.md).

> Migration status: the crawl-storage hard cut is complete. Crawl history lives only in DuckLake
> and raw HTML lives only in the configured repository; the Postgres crawl, observed-URL, and
> artifact models are gone. Task/effect runs and leases still use Postgres, while JetStream carries
> short-lived progress, until the separate execution-state cutover. Repository ingestion is
> already a dedicated JetStream work queue with a separate DuckLake writer process.

## Product Boundary

Atlas is a web acquisition and normalization product. It turns captured HTML into a stable,
loss-minimized DOM representation that can be queried and exported through SQL/Arrow/Parquet.
External systems own domain-specific cleaning, labeling, feature engineering, tokenization, and ML
training.

Atlas is a complete standalone product because it owns its durable repository and discovery/query
surface. It remains interoperable by retaining canonical HTML and exporting standard formats.

## State Ownership

Atlas assigns every kind of state one authoritative owner:

```text
Postgres
  atlas database: user-editable tasks, task effects, schemas, policies, URL matches
  atlas_catalogue database: DuckLake-owned metadata and snapshot coordination

NATS JetStream
  task runs, effect runs, ingestion jobs, recent lifecycle/progress,
  cancellation, worker presence, and active-run fencing

DuckLake
  durable crawls, documents, DOM elements, run manifests, and run/crawl usage

Filesystem or S3-compatible object storage
  content-addressed canonical HTML.zst and optional debug media

Prometheus
  operational metric history and alerting
```

The `atlas` Postgres database is a small relational control plane. It is not the target work queue,
execution ledger, artifact registry, or crawl-history database. The separate `atlas_catalogue`
database is an implementation component of DuckLake: Atlas ORM models and Alembic never manage it,
and large crawl/document/element data remains in DuckLake-managed Parquet.

## Execution Boundary

Atlas has exactly one action-execution path. API and CLI processes never execute actions, browser
work, crawls, schema generation, or extraction locally.

1. A client submits a typed action request to `atlas-api`.
2. The API resolves or creates the reusable task definition in Postgres, freezes its revision and
   referenced configuration into a run envelope, publishes that envelope to the JetStream work
   stream, and returns `202 Accepted` with `task_id` and `run_id`.
3. `atlas-worker` claims work through a shared durable pull consumer and executes it through the
   common task executor.
4. The worker publishes lifecycle/progress events and updates bounded latest state in JetStream KV.
5. Crawl output is committed through an idempotent repository-ingestion job: canonical HTML goes
   to the configured object store and crawl/document/element rows go to DuckLake.
6. The API relays retained/live events over SSE. Durable crawl results resolve to DuckLake
   crawl/document identities; large structural results are queried or exported from the repository.

JetStream work delivery is at least once. Run IDs, crawl IDs, content hashes, KV compare-and-swap
tokens, and DuckLake ingestion logic must make every externally visible side effect idempotent and
fence stale attempts.

## Definitions and Executions

Tasks and task effects are user-editable definitions in Postgres. Task runs and effect runs are
temporal executions in JetStream.

Every queued run carries a frozen execution envelope containing at least:

- run and task identity
- task revision or definition hash
- primitive and validated input snapshot
- trigger and deterministic scheduled occurrence identity where applicable
- referenced policy/schema revisions
- effect definitions or revision references according to the chosen evaluation semantics

Workers do not execute the latest mutable task definition after claiming a queued run. Editing a
task cannot change already queued work.

Scheduled occurrences use deterministic identities. Re-publishing an occurrence after a scheduler
crash must resolve to the same logical run. Effect application and downstream publication are also
idempotent; an effect work item is acknowledged only after its Postgres mutation and required
downstream publications succeed.

## JetStream Topology

The target uses separate constructs for separate temporal concerns:

- A file-backed work stream with durable pull consumers and explicit acknowledgements carries task,
  effect, and repository-ingestion jobs.
- A limits-retained event stream carries lifecycle and progress history for SSE and recent
  operations.
- JetStream KV carries latest run state, active-task locks/fencing tokens, cancellation requests,
  and worker presence/capacity.

A work message is acknowledged only after its durable side effects commit. Stream limits must not
silently discard unprocessed work; production uses safe retention, bounded redelivery, dead-letter
handling, and replication appropriate to the deployment.

Task/effect event retention is finite. A durable DuckLake crawl row copies the frozen provenance
needed to understand an acquisition after its run events expire.

## Repository

Canonical captured HTML is compressed as a content-addressed `.html.zst` object under a relative
repository key. The configured repository root is either a local filesystem directory or an
S3/S3-compatible prefix.

DuckLake owns five initial logical tables:

- `documents`: immutable HTML content identity/raw object reference plus current projection recipe
- `crawls`: acquisition event and frozen execution/configuration provenance
- `elements`: one replaceable, deterministic, loss-minimized DOM materialization per document
- `run_manifests`: frozen corpus-facing run input for runs with durable crawl usage
- `run_crawl_usages`: exact association from each run to fresh or cache-reused crawl/document data

Run manifests are not execution state. Attempts, status, progress, cancellation, errors, and
worker fencing remain in JetStream; the durable manifest and usage relation preserve only the
provenance needed to resolve a catalogue result after temporal execution state expires.

Atlas does not retain parallel projection versions. If the active parser or encoder recipe changes,
canonical HTML is reparsed and that document's element rows and recipe metadata are replaced
atomically. DuckLake snapshots are operational history, not part of the projection query contract.

DuckLake owns its managed Parquet filenames, snapshots, partitions, sorting, and compaction under
an opaque `lake/` data path. Atlas never constructs one permanent catalog Parquet file per crawl.

A page-local `dom.parquet` exists only inside the dedicated ingestor as bounded staging. It is
deleted after its rows commit to DuckLake. Raw HTML and the DuckLake-managed element
representation are the two permanent data representations.

Atlas has no Postgres `artifacts` or `task_run_artifacts` tables. Document/repository APIs
replace artifact APIs, and public responses never expose local filesystem paths.

See [`STORAGE.md`](STORAGE.md) for the schema, object layout, ingestion lifecycle, maintenance,
retention, and migration rules.

## Public API

Typed action submission endpoints remain asynchronous:

- `POST /search`
- `POST /index`
- `POST /crawl`
- `POST /schema`
- `POST /extract`
- `POST /calibrate`

They return a common queued-run contract and `Location: /task-runs/{run_id}`. Run endpoints expose
bounded current/recent state from JetStream and never embed large output arrays in polled status.

Repository endpoints evolve around durable identities:

- `GET /crawls/{crawl_id}`
- `GET /documents`
- `GET /documents/{document_id}`
- `GET /documents/{document_id}/html`
- `GET /documents/{document_id}/elements`

Advanced structural queries are read-only and bounded by time, memory, result rows, and scanned
data. Large results are streamed or exported.

## Progress and Cancellation

Actions receive a transport-independent `ProgressReporter`. General progress events use a defined
phase taxonomy and bounded structured metadata. Event IDs are retry-safe and support SSE replay
while the event stream retains them.

Cancellation is cooperative and stored in JetStream KV. Queued work can be terminated before
claim; running work stops at explicit reporter checks, capacity waits, or worker supervision
boundaries. Disconnecting an SSE client does not cancel execution.

Progress and lifecycle publication are part of the temporal execution experience, but Prometheus
metric delivery remains best effort and never decides business correctness.

## Execution Lifecycle

Each action run executes in an isolated, killable subprocess with its own async event loop and
process group. Synchronous database, browser, provider, or repository stalls therefore cannot
block the worker supervisor, other slots, recovery, or graceful shutdown. Terminating a run kills
the complete process group so Playwright drivers and browser descendants cannot outlive it.

JetStream acknowledgement deadlines provide redelivery, while an attempt-scoped KV token fences
terminal state. A stale child may not overwrite a newer attempt or commit a second logical crawl.
The worker supervisor alone reports worker presence, capacity, and active slots.

Worker maintenance removes orphaned Atlas Playwright task trees and, when safe, detached Chromium
profile trees. Operators can run the same node-local cleanup with `atlas purge playwright`.

No Postgres, DuckLake, or catalog transaction may remain open across browser work, LLM calls,
capacity waits, or other unbounded awaits.

## Concurrency and Crawl Capacity

Each worker supervises a bounded number of task runs configured by `ATLAS_WORKER_CONCURRENCY`.
Multiple workers share the JetStream durable pull consumer.

Task concurrency is separate from page-acquisition concurrency. Each run consumes its frontier
through a bounded worker queue configured by `ATLAS_CRAWL_CONCURRENCY_PER_RUN`; frontier size never
determines live coroutine or browser count. Page workers in one task share a concurrency-safe
crawler for each transport mode, so a normal single-mode run opens one browser process while using
bounded parallel page contexts.

Every task-scoped browser instance requires a deployment-wide browser permit. A matching
CrawlPolicy's `max_concurrency` remains scoped to each URL acquisition, and the policy's URL match
is the shared limit across runs and workers.

The current implementation uses renewable Postgres permit leases. Whether browser and crawl-policy
permits move to JetStream KV is deliberately undecided; the storage architecture must not silently
make that choice. Waiting remains asynchronous, cancellation-aware, observable, and recoverable.

## CLI

The `atlas` CLI is an HTTP/SSE client. `atlas init --url <api-url>` writes `atlas.json` in the
current project directory, and configuration resolves in this order:

```text
--api-url > ATLAS_API_URL > nearest atlas.json
```

The CLI does not connect directly to Postgres, JetStream, DuckLake, or repository storage.
Node-local process maintenance is the deliberate exception: `atlas purge playwright` acts on the
machine where it runs because an API pod cannot clean worker processes on another node.

## Code Ownership

- `backend/actions/` owns primitive behavior.
- `backend/tasks/` owns task/effect definitions, run envelopes, scheduling semantics, and execution.
- `backend/worker/` owns JetStream consumption, worker supervision, and isolated execution.
- `backend/catalogue/` owns DuckLake client configuration, the logical schema contract, and
  bootstrap/inspection. Deployment bootstraps the dedicated PostgreSQL catalogue before workers
  start; task subprocesses attach and validate but do not run catalogue DDL.
- `backend/dom/` owns the DOM schema, bounded Arrow encoder, page-local reader, and structural query helpers.
- `backend/repository/` owns raw object storage, the durable ingestion queue/writer, and maintenance around
  the catalogue.
- `backend/api/` validates HTTP input and exposes temporal/repository state; it does not execute
  actions.
- `backend/cli/` talks only to the HTTP API.

Crawl remains the page-acquisition chokepoint. Shared page-loading configuration stays in
`actions.shared.crawl`; no action creates a parallel browser execution path.

## Operational Metrics

Prometheus owns aggregation, history, dashboards, and alerts. Atlas exposes cluster state from the
authoritative owner of each metric: JetStream for work/runs/workers, Postgres for editable
configuration and currently Postgres-backed permits, DuckLake for durable crawl/repository state,
and repository/object-store instrumentation for ingestion and maintenance.

The in-app `/scheduled-work/metrics` hub reuses the same typed semantic snapshot logic and may add
bounded recent state or Prometheus queries. CRUD/resource pages remain focused on records rather
than embedding analytics bands. The full contract is in [`METRICS.md`](METRICS.md).

Correctness never depends on metric delivery.
