# Lifecycle

Atlas keeps acquisition small and moves rebuildable work off the hot path.

## 1. Ingestion

A crawl produces visits. A visit may require several attempts and may produce one document.
The immutable `ingest.crawls` row is written once, when the crawl reaches its terminal state.
Traversal and frontier state remain operational and are not copied into the ingestion schema.

Acquisition:

1. Acquires the destination.
2. Stores the document bytes immutably in object storage.
3. Prepares the minimal navigation package required to continue the crawl.
4. Atomically commits the visit, attempts, steps, and document reference.
5. Moves on without waiting for document interpretation.

A visible document reference must always point to durable bytes.

## 2. CDC

Change data capture is the glue between committed evidence and derived relations.

A committed `ingest.documents` change schedules projection work according to the document's
detected media type.

Projection identity includes the document content identity, projection type, and projector version.
Partial projections never become queryable.

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
ingest.documents CDC       -> links_lane         -> material.links
```

The links workload reads visits, documents, and reusable HTML projections. If a required projection
is not ready, it retries the document change rather than subscribing to a second CDC source.

`material.links` deduplicates normalized source and target URL pairs, classifies their deterministic
site relationship, and retains their first and last positive observation times. It does not create
page rows for unvisited targets.

Materialization failure never changes the committed ingestion record. Work can be replayed from
CDC or rebuilt from upstream relations.

## 4. Query

Users query `web.*`, not the physical plan.

The compiler uses `material.pages` and `material.links` to identify relevant pages, visits, and
documents before scanning detailed structural relations. This makes cross-site graph and document
queries practical.

Users persist their own interpretations under `data.*`.
