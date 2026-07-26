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
4. Atomically commits the visit, attempts, steps, document reference, and document event.
5. Moves on without waiting for document interpretation.

A visible document reference must always point to durable bytes.

## 2. CDC

Change data capture is the glue between committed evidence and derived relations.

A committed `ingest.documents` change schedules projection work according to the document's
detected media type. Consumers may run independently, coalesce changes, and retry safely because
the source bytes are immutable.

Projection identity includes the document content identity, projection type, and projector version.
Partial projections never become queryable.

## 3. Materialization

Atlas decodes documents into rebuildable structural relations:

```text
HTML -> material.html_elements
XML  -> material.xml_elements
JSON -> material.json_values
PDF  -> material.pdf_blocks
```

Atlas also maintains compact semantic indexes:

```text
ingest.visits -> material.pages

ingest.visits + material.html_elements
    -> material.links
```

`material.links` deduplicates source and target URL pairs and may retain `first_seen`,
`last_seen`, and an observation count.

Materialization failure never changes the committed ingestion record. Work can be replayed from
CDC or rebuilt from `ingest.*`.

## 4. Query

Users query `web.*`, not the physical plan.

The compiler uses `material.pages` and `material.links` to identify relevant pages, visits, and
documents before scanning detailed structural relations. This makes cross-site graph and document
queries practical.

Users persist their own interpretations under `data.*`.
