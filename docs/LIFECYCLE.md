# Lifecycle

Atlas keeps acquisition small and moves rebuildable work off the hot path.

## 1. Ingestion

A native crawl or external load produces visits. A visit may require several attempts and may
produce one document. External evidence may omit attempts it did not retain.
The immutable `ingest.crawls` row is written once, when the crawl reaches its terminal state.
Traversal and frontier state remain operational and are not copied into the ingestion schema.

Acquisition:

1. Acquires the destination.
2. Stores the document bytes immutably in object storage.
3. Prepares the minimal navigation package required to continue the crawl.
4. Atomically commits the visit, attempts, steps, and document reference.
5. Moves on without waiting for document interpretation.

External loading:

1. Accepts exact HTML response bytes and truthful source metadata.
2. Stores the bytes immutably.
3. Creates stable imported visit and document identities with typed provenance.
4. Publishes the same ingestion work as acquisition.
5. Moves on without waiting for materialization.

A visible document reference must always point to durable bytes.

For a branch node, each outgoing plan edge executes a bounded query over that
navigation package. Selected URLs are normalized and deduplicated across the
entire run before admission to the target node. Edge execution does not read
the historical catalogue.

## 2. CDC

Change data capture is the glue between committed evidence and derived relations.

A committed `ingest.documents` change schedules projection work according to the document's
detected media type.

Projection identity includes the document content identity, projection type, and projector version.
Partial projections never become queryable.

The Basin DML event supplies the changed source table and DuckLake snapshot window. The document
workload pins all source reads to the end of that window, reads and parses each affected HTML body
once, and emits typed Arrow tables for every enabled document projection. The visit workload
resolves changed visit identities at the same pinned snapshot and replaces corrected or deleted
observation slices. Repeated delivery is idempotent, and no target-to-target CDC hop exists.

A materialization workload defines:

```text
ingest.documents CDC -> one-pass document projection
                     -> HTML elements + JSON-LD + links + link observations
ingest.visits CDC    -> one visit projection
                     -> pages + page observations
```

Each source workload owns one consumer and ACKs only after every enabled target commits. A replay
starts from the same source window and skips or merges completed output. Selection borrows one
client, local projection uses a fixed CPU worker set, and independent partition writers borrow up
to the process-wide eight-client pool. The shared `select -> local project -> write` contract
provides source bounds, lake-bucket grouping, backpressure, and timing for CDC and maintenance.
Document Arrow output is divided into no more than eight stable write partitions with a soft
32 MiB target, so useful concurrency grows with output volume without producing 64 tiny writes.

Backfills and rebuilds invoke the same source-owned workload plans in bounded batches. Postgres
stores the pinned source snapshot, workload cursor, projector versions, shadow destinations, and
counters. Any table can be selected independently; selected tables from the same source share one
scan and projection pass. Backfills fill missing live slices. Rebuilds target shadows, catch up
post-snapshot changes, and activate idempotently only after a source high-water recheck under the
ordinary target leases.

## 3. Materialization

Atlas decodes documents into rebuildable structural relations:

```text
HTML -> material.html_documents
HTML -> material.html_elements
HTML -> material.jsonld_values
```

Generic JSON, XML, PDF, DOCX, CSV, and other format projections are deferred.

Atlas also maintains compact semantic indexes:

```text
ingest.documents CDC -> material.html_documents
                     -> material.html_elements
                     -> material.jsonld_values
                     -> material.links
                     -> material.link_observations
ingest.visits CDC    -> material.pages
                     -> material.page_observations
```

The document workload derives links directly from the same locally parsed elements used for the
HTML and JSON-LD outputs. Links never wait for or read another materialized target.

`material.page_observations` connects each normalized page identity to its visit, optional document,
and observation time. `material.links` deduplicates normalized source and target URL pairs and
classifies their deterministic site relationship. `material.link_observations` retains the
document, content hash, element index, raw href, and observation time for every anchor occurrence.
Its source and target page identities are deterministic even when the target has never been
visited; it does not create page rows for unvisited targets.

Materialization failure never changes the committed ingestion record. Work can be replayed from
CDC or rebuilt from upstream relations.

## 4. Query

Users query the public `web.*` and `dom.*` catalogue, not the physical plan.

Atlas initialization installs and versions both public namespaces as persistent DuckLake views
and macros over the physical evidence and materialization schemas. Expensive recurring
computations become fixed materializations. A later native optimizer may accelerate measured plan
gaps without defining query semantics. See [`QUERY.md`](QUERY.md).

Users persist their own interpretations under `data.*`.
