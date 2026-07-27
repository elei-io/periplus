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

The Basin DML event supplies the changed table and DuckLake snapshot window. HTML materialization
queries DuckLake's data change feed for that window, reduces changed document rows to affected
content hashes, parses only live hashes not already present in `material.html_elements`, and
commits only those rows. Hashes no longer referenced by HTML documents are removed by a scoped
merge. Startup reconciliation compares source and target hash identities rather than rebuilding
covered projections.

JSON-LD replaces only affected content-hash slices after `material.html_elements` changes. Pages
merge deterministic identities for changed observed visit URLs. Links read only changed HTML
document observations, wait until their content hashes are present in `material.html_elements`,
and merge source-target pairs using `least(first_seen_at)` and `greatest(last_seen_at)`. Repeated
delivery and out-of-order imported observations are therefore idempotent without rescanning prior
visits. Existing durable consumers resume their acknowledged sequence on process restart; a full
non-HTML backfill runs only for a new consumer.

A materialization workload defines:

```text
CDC source -> workload logic -> owned material.* target
```

Each workload owns one consumer and writes only its own target relation. It may read other
relations, coalesce changes, and retry until its dependencies are available. It never creates or
updates rows in another materialization.

## 3. Materialization

Atlas decodes documents into rebuildable structural relations:

```text
HTML -> material.html_elements
HTML -> material.jsonld_values
```

Generic JSON, XML, PDF, DOCX, CSV, and other format projections are deferred.

Atlas also maintains compact semantic indexes:

```text
ingest.documents CDC       -> html_elements_lane -> material.html_elements
material.html_elements CDC -> jsonld_values_lane -> material.jsonld_values
ingest.visits CDC           -> pages_lane         -> material.pages
ingest.visits CDC           -> page_observations_lane
                           -> material.page_observations
ingest.documents CDC       -> links_lane         -> material.links
```

The links workload reads visits, documents, and reusable HTML projections. If a required projection
is not ready, it retries the document change rather than subscribing to a second CDC source.

`material.page_observations` connects each normalized page identity to its visit, optional document,
and observation time. `material.links` deduplicates normalized source and target URL pairs,
classifies their deterministic site relationship, and retains their first and last positive
observation times. Its source and target page identities are deterministic even when the target
has never been visited; it does not create page rows for unvisited targets.

Materialization failure never changes the committed ingestion record. Work can be replayed from
CDC or rebuilt from upstream relations.

## 4. Query

Users query `web.*`, not the physical plan.

The compiler uses `material.pages`, `material.page_observations`, and `material.links` to identify
relevant pages, visits, and documents before scanning detailed structural relations. This makes
cross-site graph and document queries practical.

Users persist their own interpretations under `data.*`.
