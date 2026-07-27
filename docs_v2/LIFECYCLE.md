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

## 2. CDC

Change data capture is the glue between committed evidence and derived relations.

A committed `ingest.documents` change schedules projection work according to the document's
detected media type.

Projection identity includes the document content identity, projection type, and projector version.
Partial projections never become queryable.

The Basin DML event supplies the changed source table and DuckLake snapshot window. The document
workload reduces changed document rows to affected content hashes, commits HTML elements, replaces
their JSON-LD slices, streams any new normalized link pairs, and replaces each changed document's
anchor observations. The visit workload merges page identities and page observations for changed
visits. Repeated delivery is idempotent, and duplicate document content does not create a
target-to-target CDC hop.

A materialization workload defines:

```text
ingest.documents CDC -> HTML elements -> JSON-LD
                                      -> links + link observations
ingest.visits CDC    -> pages -> page observations
```

Each source workload owns one consumer and its fixed target stages. It coalesces changes and ACKs
only after every stage commits. A replay starts from the same source window and safely skips or
merges completed stage output. Source ownership does not pin a DuckBasin client: stages borrow
from the process-wide eight-client pool. Pages and page observations run concurrently. JSON-LD
and links run concurrently after HTML elements commit. I/O-heavy stages declare bounded
`select -> project -> serialized write` plans so source-byte partitioning, shared-lane scheduling,
backpressure, progress timing, and target-scoped retry behavior are consistent across current and
future fixed materializations.

Backfills and rebuilds invoke the same fixed stage functions in bounded batches. Postgres stores
the pinned source snapshot, stage cursor, projector version, shadow destinations, and counters.
Backfills target live tables. Rebuilds target shadows, catch up post-snapshot source changes in
bounded batches, and atomically activate only after a source high-water recheck under the ordinary
target lease.

## 3. Materialization

Atlas decodes documents into rebuildable structural relations:

```text
HTML -> material.html_elements
HTML -> material.jsonld_values
```

Generic JSON, XML, PDF, DOCX, CSV, and other format projections are deferred.

Atlas also maintains compact semantic indexes:

```text
ingest.documents CDC -> material.html_elements
                     -> material.jsonld_values
                     -> material.links
                     -> material.link_observations
ingest.visits CDC    -> material.pages
                     -> material.page_observations
```

The document workload's links stage reads visits, documents, and reusable HTML projections. If a
required projection is not ready, it retries the document change rather than subscribing to a
second CDC source.

`material.page_observations` connects each normalized page identity to its visit, optional document,
and observation time. `material.links` deduplicates normalized source and target URL pairs and
classifies their deterministic site relationship. `material.link_observations` retains the
document, content hash, element index, raw href, and observation time for every anchor occurrence.
Its source and target page identities are deterministic even when the target has never been
visited; it does not create page rows for unvisited targets.

Materialization failure never changes the committed ingestion record. Work can be replayed from
CDC or rebuilt from upstream relations.

## 4. Query

Users query `web.*`, not the physical plan.

The compiler uses `material.pages`, `material.page_observations`, and `material.links` to identify
relevant pages, visits, and documents before scanning detailed structural relations. This makes
cross-site graph and document queries practical.

Users persist their own interpretations under `data.*`.
