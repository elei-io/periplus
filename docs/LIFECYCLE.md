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

## 2. Rebuild scheduling

A rebuild pins one DuckLake snapshot and scans `ingest.visits` into bounded visit-ID batches. The
visit is the only workload unit. When it references an HTML document, that same batch reads the
immutable bytes once and emits every document projection:

```text
ingest.visits -> page projection
              -> pages + page observations + page heads
              -> optional one-pass document projection
              -> content stats + HTML elements + JSON-LD
              -> links + link occurrences
```

Workers project locally. Large outputs become final bucketed Parquet files under the permanent
`material/data` namespace and are registered with `ducklake_add_data_files`; pages, page heads, and
link identities use `MERGE INTO`. The complete batch plus its applied marker commits in one
DuckLake transaction. JetStream is delivery only and is ACKed after commit, so a restart between
commit and ACK reads the marker and completes without duplicating logical output.

Postgres durably records the run, hidden table names, batch IDs, progress, and failures. After the
initial batches complete, Atlas detects inserted visits through DuckLake snapshot changes and
repeats bounded catch-up until the source high-water mark is covered. One serialized activation
consumer atomically swaps all material tables. Batch consumers remain horizontally scalable.
The swap retains the superseded tables as activation replay markers until Postgres records the run
as completed. Atlas then drops those retired tables before acknowledging the activation delivery;
a completed-run redelivery retries that idempotent finalization. LakeDucktor can reclaim the
resulting unreferenced physical files.
Each plan, batch, and activation record also stores whether JetStream accepted its publication.
Recovery publishes only records that were never accepted; JetStream redelivery owns published
work that has not been ACKed.

A deterministic projection or schema failure terminates the rebuild immediately. Atlas drops the
failed hidden generation idempotently and removes only transient sidecars owned by the failed
batch. LakeDucktor reclaims registered and unreferenced final files.

After activation, a dedicated connection holds one insert-only DuckLake CDC consumer for
`ingest.visits`. It opens one bounded snapshot window, divides its visit IDs into deterministic
batches, and publishes them to the existing JetStream batch subject. The same horizontally
scalable workers project and commit those batches against the active generation. The coordinator
advances the CDC cursor only after every applied-batch marker is visible. A crash before cursor
commit replays the same deterministic batch IDs and is therefore a no-op after commit.

Activation records the active generation, covered source snapshot, and batch size in DuckLake.
That single row fences superseded work and is enough to recover live maintenance; Postgres does
not contain a live-work ledger. A new rebuild catches up through its activation high-water mark
before atomically replacing that row and all material tables.

If live maintenance finds a missing or unreadable registered Parquet file, the entire material
generation is corrupt. Before ACKing that poisoned delivery, Atlas durably ensures one complete
rebuild exists in Postgres and deletes the matching active-generation row. The CDC coordinator
therefore abandons its uncommitted window, while the rebuild recovers every affected visit from
immutable `ingest.*`. Activation installs a fresh generation and a fresh generation-scoped CDC
consumer. Atlas never attempts per-table or per-file material repair.

## 3. Materialization

Atlas decodes documents into rebuildable structural relations:

```text
HTML -> material.content_stats
HTML -> material.html_elements
HTML -> material.jsonld_values
```

Generic JSON, XML, PDF, DOCX, CSV, and other format projections are deferred.

Atlas also maintains compact semantic indexes:

```text
ingest.visits -> material.pages + material.page_observations + material.page_heads
              -> optional document
              -> material.content_stats + material.html_elements
              -> material.jsonld_values + material.links
              -> material.link_occurrences
```

The document workload derives link occurrences and narrow link-identity sidecars from the same
locally parsed elements used for the HTML and JSON-LD outputs. Once every occurrence batch is
committed, finalization deduplicates the sidecars and computes `material.links` exactly once from
the completed `material.link_occurrences` generation.

`material.page_observations` connects each normalized page identity to its visit, optional document,
and terminal ordering time. `material.page_heads` stores the deterministically latest visit pointer,
including failures. `material.links` deduplicates normalized source and target URL pairs, classifies
their deterministic site relationship, and stores exact occurrence-derived rollups.
`material.link_occurrences` retains the visit, document, content hash, element index, raw href, and
observation time for every anchor occurrence.
Its source and target page identities are deterministic even when the target has never been
visited; it does not create page rows for unvisited targets.

Materialization failure never changes committed ingestion evidence. Prometheus and structured logs
expose queue state, rebuild progress, projection/Parquet/commit duration, source and output
throughput, conflicts, retries, and failures.

## 4. Query

Users query the public `web.*` and `dom.*` catalogue, not the physical plan.

Atlas initialization installs and versions both public namespaces as persistent DuckLake views
and macros over the physical evidence and materialization schemas. Expensive recurring
computations become fixed materializations. The optional native extension supplies standards-shaped
DOM selectors and shared query diagnostics; base catalogue semantics do not depend on it. See
[`QUERY.md`](QUERY.md).

Users persist their own interpretations under `data.*`.
