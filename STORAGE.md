# Atlas Storage Architecture

This document is the contract for Atlas storage, repository ingestion, and state ownership.

> Implementation status: the crawl-storage hard cut is complete. Task-backed captures store
> content-addressed HTML through `backend/repository/`, project the v1 DOM, and commit
> document/crawl/element records through `backend/catalogue/`. Cache resolution, structural
> results, provenance, and crawl history use DuckLake exclusively. Postgres artifact, crawl, and
> URL-history tables and APIs have been removed. Task/effect execution remains Postgres-backed
> until the separate JetStream execution-state cutover. Repository ingestion already uses its
> own file-backed JetStream work stream and dedicated DuckLake writer.

## Product Boundary

Atlas is the acquisition and normalization layer between the web and SQL-centric data/ML
pipelines:

```text
web
  -> Atlas acquisition
  -> canonical HTML + loss-minimized DOM representation
  -> DuckLake query and export surface
  -> external cleaning, labeling, feature, and training pipelines
```

Atlas owns faithful acquisition, deterministic HTML-to-DOM conversion, repository cataloging,
structural query primitives, and export. It does not own domain-specific quality policy,
boilerplate removal, semantic deduplication, labeling, embeddings, tokenization, feature
engineering, dataset splits, or model training.

The long-lived product API is the stable DOM representation and its query semantics. Parquet,
DuckLake, local filesystems, and S3 are implementations of that contract.

## State Ownership

Each kind of state has one authoritative owner.

### Postgres: user-editable control plane

- `tasks`
- task effect definitions (`task_effects` in the current schema)
- `query_schemas`
- `data_schemas`
- `crawl_policies`
- `url_matches`
- repository/destination configuration when it becomes user-editable

Tasks and effects are definitions, not executions. They remain relational because users create,
edit, schedule, archive, order, and reference them. Queued work must carry a frozen task revision
and frozen effect/schema/policy references so later edits cannot alter an existing run.

### NATS JetStream: temporal execution plane

- task runs
- effect runs
- repository ingestion jobs
- lifecycle and progress events
- cancellation requests
- worker presence and capacity
- active-run ownership and fencing

The target uses separate JetStream constructs rather than treating one stream as both queue and
database:

- A file-backed work stream with durable pull consumers and explicit acknowledgements carries
  task, effect, and ingestion jobs.
- A bounded event stream carries lifecycle and progress history for SSE, recent operations, and
  observability.
- JetStream KV carries latest run state, active-task locks, cancellation state, and worker
  presence. Compare-and-swap revisions fence stale attempts.

Work handlers are idempotent. A message is acknowledged only after its required durable side
effects commit. JetStream delivery is at least once; neither object-store writes nor DuckLake
transactions may depend on exactly-once delivery.

Task and effect run history is intentionally bounded. Anything required to reproduce a crawl is
copied into the durable DuckLake crawl record before temporal history expires.

### DuckLake: durable historical and analytical plane

- `crawls`
- `documents`
- `elements`
- `run_manifests`
- `run_crawl_usages`

DuckLake is Atlas's durable corpus and query surface. Its catalog owns logical tables, snapshots,
schema evolution, partitions, sort order, and managed Parquet files. Atlas must not construct or
depend on DuckLake's physical filenames.

DuckLake metadata is stored in a dedicated PostgreSQL database named `atlas_catalogue` by default.
PostgreSQL is the chosen catalogue backend for local Compose and production because Atlas executes
task runs in independent processes and DuckDB file catalogues permit only one writer process. The
control-plane and DuckLake catalogue may share one PostgreSQL server, but they remain separate
databases with independent migrations, permissions, backups, and operational metrics. DuckLake,
not the Atlas ORM or Alembic, owns the contents of `atlas_catalogue`.

### Repository object storage: canonical raw plane

- content-addressed compressed HTML
- optional debug media with an independent retention policy

Raw HTML is stored as `.html.zst`, not duplicated inside DuckLake. The `documents` table stores a
relative object key and content metadata. The repository root may be a local filesystem for local
development or an S3/S3-compatible prefix for a durable deployment.

## Repository Layout

The logical layout is identical across backends.

Filesystem:

```text
<repository-root>/
  raw/html/sha256/<first-2>/<next-2>/<sha256>.html.zst
  lake/                  # opaque DuckLake data path
  staging/               # temporary, crash-recoverable ingestion data
  debug/                  # optional, separately retained
```

S3-compatible storage:

```text
s3://<bucket>/<prefix>/
  raw/html/sha256/<first-2>/<next-2>/<sha256>.html.zst
  lake/                  # opaque DuckLake data path
  staging/               # temporary ingestion objects if required
  debug/
```

DuckLake rows store relative keys such as
`raw/html/sha256/88/c7/88c7...html.zst`, not deployment-specific absolute paths. Repository
configuration resolves keys against the filesystem or S3 root.

DuckLake records its data path in catalogue metadata. Disk deployments default
`ATLAS_CATALOGUE_OVERRIDE_DATA_PATH=true` so a host path and its Compose bind-mount path may attach
to the same physical repository. This option must not be used to point clients at different disk
contents. S3 deployments retain strict path matching by default.

`ATLAS_REPOSITORY_STORAGE=disk|s3` is the single deployment-level backend selection for both raw
objects and DuckLake data. `ATLAS_REPOSITORY_ROOT` supplies the filesystem root; the
`ATLAS_REPOSITORY_S3_*` family supplies the S3 bucket, prefix, endpoint, region, credentials,
addressing style, and TLS choice. DuckLake derives its data location as `<root>/lake` or
`s3://<bucket>/<prefix>/lake`. Do not add a separate catalogue storage-backend setting: allowing
raw HTML and the lake to select independently makes partial or ephemeral deployments too easy.

Do not ZIP crawl bundles. HTML is compressed directly with Zstandard, Parquet is already
compressed, and debug media remains independently addressable. ZIP archives obstruct partial
reads, lifecycle rules, checksums, and direct object access.

## Canonical HTML

The HTML object is the irreplaceable source from which derived DOM data can be regenerated.

Atlas must explicitly define what it captures. Crawl4AI's rendered `result.html` is not
necessarily the original HTTP response body. Until Atlas deliberately captures network bytes,
the canonical source is the exact UTF-8 byte encoding of the rendered HTML string returned by the
crawl layer. Documentation and APIs must call it rendered/captured HTML rather than original
response bytes.

Content identity is the SHA-256 of the uncompressed canonical bytes. The object key is derived
from that hash. Upload is idempotent: if the object already exists with the expected identity,
Atlas streams and verifies its decompressed hash and byte length before reusing it. Repository
ingestion and metadata-only cache reads retain the same verification boundary, so an existing
document projection cannot silently hide a missing, malformed, or corrupt canonical object.

Store at least:

- uncompressed SHA-256
- uncompressed byte length
- compressed byte length
- compression (`zstd`)
- media type and character encoding

`document_id` is `sha256:<html_sha256>`. Atlas deliberately uses the canonical content identity as
the document identity instead of maintaining and querying a second uniqueness key. Identical
canonical HTML therefore shares one document, one object, and one element projection by
construction.

## DuckLake Logical Model

### `documents`

One row represents one unique canonical HTML document:

```text
document_id
html_sha256
html_object_key
html_content_type
html_encoding
html_size_bytes
html_compressed_size_bytes
compression
dom_schema_version
parser_name
parser_version
parser_options_hash
element_count
created_at
```

The document's canonical identity and raw-object metadata are immutable. Atlas maintains exactly
one current element projection for that document. Projection recipe fields (`dom_schema_version`,
parser identity/options, and `element_count`) describe the current derived materialization and may
change only as part of an atomic projection replacement.

### `crawls`

One row represents one acquisition event. Successful acquisitions and failures that produced
captured HTML reference a document. Failures that produced no HTML retain `document_id = NULL` so
DNS errors, browser crashes, navigation timeouts, and similar events remain part of durable crawl
history:

```text
crawl_id
document_id    # nullable only when the acquisition produced no captured HTML
run_id
task_id
task_revision
primitive
requested_url
normalized_url
final_url
captured_at
status_code
duration_ms
input_json
input_hash
crawl_policy identity/revision
schema identities/revisions
warnings/errors
```

The exact physical columns may be refined, but a crawl must retain enough frozen provenance to be
understood after its JetStream run state has expired and after editable Postgres definitions have
changed. A documentless crawl must contain at least one error. It is never cache-eligible and has
no element projection.

Task and CrawlPolicy definitions carry monotonically increasing revisions. A task run snapshots
the task revision when queued; each crawl stores that frozen revision and the revision of the
matching CrawlPolicy used for acquisition. Schema IDs on a crawl describe schemas that actually
participated in acquisition and are not populated retroactively for schemas generated afterward.
Generated data and query schemas instead retain their own source crawl and document identities.

### `run_manifests`

One row records the immutable, corpus-facing part of a run that actually used durable crawl data:

```text
run_id
task_id
task_revision
primitive
input_json
queued_at
```

This is not a task execution ledger. Status, attempts, progress, cancellation, worker ownership,
errors, and effect execution remain temporal JetStream state. The manifest preserves the frozen
input needed to understand a durable catalogue result after temporal run state expires. Runs that
never use a durable crawl do not require a manifest.

### `run_crawl_usages`

One row associates a run with a crawl it actually consumed, including cache reuse:

```text
usage_id
run_id
crawl_id
document_id  # nullable for a documentless failed acquisition
requested_url
normalized_url
source       # network, repository, or stale_if_error
role         # primitive_result, index_traversal, calibration_candidate/result, schema_generation_input
ordinal      # zero-based original input, traversal, or candidate position within the role
returned     # whether this usage supplied the usable value returned by the run
```

`source` describes where bytes came from; it must not be overloaded with why they were consumed.
`role` and `ordinal` preserve purpose and ordering. `returned` distinguishes failed attempts and
unselected calibration candidates from the successful network/cache/stale value that contributed
to the run result. A failed terminal acquisition therefore has `returned = false`; its durable
crawl error remains the result evidence when no returned usage exists for that position.

`usage_id` is deterministic for the run, crawl, normalized URL, role, and ordinal. Fresh acquisitions commit the
manifest and usage with the crawl. Cache hits retain the original acquisition identity but append
a usage for the current run through the same dedicated repository writer. This relation is the
authoritative result locator; `crawls.run_id` identifies the run that performed the original
acquisition.

### `elements`

The page-local DOM representation is deliberately small:

```text
document_id    # injected during repository ingestion
element_index  # zero-based depth-first document order
parent_index   # nullable parent element index
tag
namespace_uri
attributes     # MAP(VARCHAR, VARCHAR)
text           # text before the first child element
tail           # text following this element inside its parent
```

Within one document, `(document_id, element_index)` is the logical identity. Sibling order is
derived by ordering children by `element_index`. Depth, selectors, paths, descendant text,
resolved URLs, class tokens, parsed styles, `inner_html`, and `outer_html` are derived query
features and are not persisted in v1.

Attribute values remain source-relative strings. Atlas does not resolve URLs, collapse
whitespace, tokenize classes, or interpret boolean attributes during projection. Absent and
present-empty attributes remain distinguishable. DOM schema v1 uses HTML5 parsing with browser
namespace semantics. Element namespace URIs are stored separately from local tag names.
Unnamespaced attribute keys remain their local names; namespaced attribute keys use Clark notation
(`{namespace-uri}local-name`). Attribute maps are emitted in key-sorted order for deterministic
encoding, although queries must not assign semantic meaning to that order.

Link projection has one storage-independent semantic path. Fresh network captures and repository
cache hits both derive links through `backend/dom/`: anchor text is complete descendant text in
document order, the first valid `<base href>` in the document head controls relative URL
resolution, URL normalization and internal/external classification are deterministic, and the
first occurrence of a normalized URL wins. Crawl4AI's transient `result.links` is not a separate
source of truth. Filesystem, S3, and DuckLake-backed reads must produce the same link payload for
the same canonical HTML and page URL.

Only elements become rows. Text adjacent to an omitted comment or other non-element child is
folded into the surrounding element's `text` or preceding child element's `tail`, preserving the
structural text stream without pretending the omitted node was persisted. HTML5 parser repairs,
implicit `html`/`head`/`body` elements, case normalization, and namespace transitions are therefore
part of v1 semantics. Parser identity, version, options hash, and DOM schema version are recorded on
the document so Atlas can determine whether the current rows are reproducible with the active
projection recipe.

The projection intentionally does not preserve source serialization details such as attribute
quoting/order, entity spelling, invalid duplicate attributes, comments, and doctypes. The
canonical HTML preserves those details. Near-zero data loss is achieved by keeping canonical HTML
and making the projection deterministic and reproducible, not by pretending a DOM table is a
byte-for-byte serialization.

### Projection Lifecycle

Atlas does not retain parallel logical projection versions and does not assign projection IDs.
Within the current schema, `(document_id, element_index)` identifies the one current materialized
projection for a document.

The element columns are deliberately small and intended to remain stable. A parser upgrade,
encoder bug fix, or changed parser option can nevertheless produce different rows without changing
those columns. Recipe metadata therefore exists to detect stale or missing materializations; it is
not a user-facing version history.

When a projection is stale or incomplete, Atlas reparses canonical HTML and atomically:

1. replaces all element rows for the document; and
2. updates the document's projection recipe metadata and element count.

Readers observe either the complete previous materialization or the complete replacement. The
document row's active recipe fingerprint is the normal-read completeness marker because document
metadata and element rows are inserted or replaced in the same DuckLake transaction. Cache and
ingestion reads must not run `count(*)` over `elements` to re-prove that invariant. `element_count`
is retained for result metadata and explicit repository validation; a maintenance audit may compare
it with physical rows to detect unsupported out-of-band mutation or storage corruption.

A corpus upgrade may rebuild stale documents lazily when used or eagerly in bounded background
batches. Documents can temporarily carry different recipe fingerprints during such a rollout, but
each document has only one current projection and its state remains detectable. DuckLake snapshots
may provide operational rollback or debugging history; they are not projection versions or part of
the normal query contract.

## Page-Local Parquet

A crawl may create a temporary page-local `dom.parquet` containing the element columns with the
already-known content-addressed `document_id`. It is an ingestion interchange unit, not a permanent second copy of the
catalog.

It may be used to:

- append a bounded group of element rows without Python object rematerialization
- combine multiple page projections into one DuckLake microbatch append
- inspect or retry an in-progress ingestion attempt before commit

Repository ingestion appends the staged rows to DuckLake and removes the staging file. Managed
DuckLake Parquet is the only permanent element representation. If it must be
rebuilt, Atlas reparses canonical HTML. Page-local Parquet can be exported again on demand.

## Ingestion and Commit Semantics

The target lifecycle is:

```text
queued
  -> captured
  -> raw_stored
  -> catalog_pending
  -> catalogued
  -> staging_evictable
```

1. When HTML was captured, the crawl computes canonical identity and writes the content-addressed
   `.html.zst` object. A documentless failure skips this step.
2. Before the first page usage for a run, it commits the compressed frozen run manifest through
   its own retry-stable ingestion operation. Page operations reference that durable manifest by
   `run_id`; they never embed the full run input.
3. It creates retry-stable `pending` state in the file-backed
   `ATLAS_REPOSITORY_RESULTS` JetStream KV bucket, then publishes a small ingestion job containing
   that frozen crawl provenance, never HTML or DOM rows.
4. The dedicated ingestor reads raw HTML, skips current duplicate projections, and otherwise
   streams bounded Arrow record batches into temporary Parquet.
5. A row/byte/item-bounded microbatch commits crawls, any associated documents/elements, and run
   usage associations to DuckLake. Documentless failures append a crawl and its usage.
6. The committed DuckLake snapshot becomes the durable catalog commit marker.
7. Atlas writes `succeeded` or `failed` terminal state to the KV bucket before acknowledging or
   terminating the work message. An ephemeral inbox notification only wakes a waiting producer;
   producers poll KV and never treat that notification as authoritative.
8. Staging is deleted immediately; a periodic age-fenced sweep removes crash leftovers.

Every serialized JetStream job and KV value is checked against
`ATLAS_NATS_MAX_ENVELOPE_BYTES` (default 900 KiB), leaving headroom below the default 1 MiB NATS
`max_payload`. Run manifests are compressed once in their dedicated operation; per-page crawl and
usage envelopes stay page-local and bounded.

If Atlas crashes after writing the HTML object but before the DuckLake commit, it leaves a harmless
unreferenced content-addressed object. A later reconciler may remove such objects after a generous
grace period. It is always safer to retain an orphan source object than to commit a document row
whose source object is missing.

Ingestion must be idempotent by `crawl_id` and document content identity. DuckLake does not enforce
primary/foreign keys, so Atlas owns duplicate prevention and relationship validation. Stable
UUIDv5 crawl identities make task retries address the same logical acquisition. With the supported
catalogues, Atlas uses `ducklake-client`'s cooperative catalogue fence around each repository
microbatch commit. The fence is backed by PostgreSQL advisory locks or local sidecar locks as
appropriate. While holding it, the writer stages each microbatch's document/hash, crawl, manifest,
and usage identities and resolves each identity set with one join against its logical table. It
never performs those analytical lookups once per page. The same transaction performs identity
checks and appends, so identical concurrent attempts collapse to one document and one crawl. DOM
staging filenames are attempt-unique so a losing or completed attempt cannot delete another
process's input.

Initial crawl ingestion is keyed by the retry-stable `crawl_id`. Pending KV state retains the
first frozen job envelope and records successful stream publication with compare-and-swap after
PubAck. A replacement task process republishes only an unconfirmed publication, then waits for the
original operation instead of reacquiring the page. A successful DuckLake crawl remains the
permanent reconciliation source after bounded KV state expires. Projection rebuilds use a separate stable
operation key derived from the document and active parser/DOM recipe, preventing an earlier crawl
success from suppressing required re-projection. The inbox is a latency optimization only: losing
the subscriber, notification, or producer process cannot lose pending or terminal ingestion state.

The dedicated ingestor claims a microbatch based on rows, bytes, item count, and a short wait
window. Preparation and commit are interleaved: as soon as the next prepared page would cross a
batch threshold, the current batch commits before preparation continues. One page may exceed a
microbatch target only when it remains within the stricter per-document HTML, element, and staging
limits. The ingestor performs one append per affected logical table inside a fenced DuckLake transaction.
If an atomic microbatch fails, the ingestor recursively isolates its entries: valid sub-batches
commit and acknowledge normally, while only individually failing messages consume redeliveries or
reach dead-letter handling. A terminal failure is copied to the file-backed
`ATLAS_REPOSITORY_DEAD_LETTER` stream before its work message is terminated. Operators inspect and
requeue the frozen job through the operations API (or its `atlas repository dead-letters` and
`atlas repository requeue <sequence>` CLI clients); successful re-publication removes that
dead-letter entry. Each failure generation uses a distinct JetStream deduplication identity so a
quickly requeued job cannot lose its replacement dead letter inside the duplicate window.
Failed parent attempts retain page-local staging until every child is committed or rejected.
Task/crawl workers write raw objects and wait for the commit result but never parse DOM for durable
storage and never write DuckLake. Data inlining remains disabled so large element payloads do not
grow the PostgreSQL metadata catalogue.

Hard per-document HTML, element-count, and staging-byte limits fail pathological pages before DOM
projection can exhaust a worker. HTML compression/decompression is streamed, Arrow conversion is
record-batch bounded, and all object-store, parser, Parquet, and DuckLake work runs outside the
crawl event loop. The HTML5 DOM tree itself remains necessarily page-sized; the HTML byte limit is
therefore the outer memory safety boundary.

## Cache Freshness and Control

Repository retention and cache freshness are independent. An old crawl may remain durable and
queryable while no longer being eligible to satisfy a new acquisition request.

The effective cache policy resolves in this order: request/task override, matching CrawlPolicy
cache defaults, then Atlas environment defaults. The initial modes are:

- `prefer`: reuse the newest fresh eligible crawl, otherwise acquire and store a new crawl.
- `refresh`: skip cache reads, acquire from the network, and store the result.
- `no_store`: skip cache reads and do not persist the acquisition.

Freshness is evaluated against `crawls.captured_at`. `ATLAS_CACHE_MAX_AGE_SECONDS` defaults to 120
seconds. A CrawlPolicy or request may set `max_age_seconds` to zero to disable reads without
changing write behavior. Cache mode and age do not participate in the acquisition `input_hash`;
they govern reuse rather than captured content.

`stale_if_error_seconds` is an optional total maximum crawl age. When no fresh entry exists, Atlas
first attempts the network and may return an eligible stale repository entry only after that
attempt fails. The stale response retains the original crawl identity and is marked
`stale_if_error`; it is never recorded as a new successful acquisition.

Cache policy resolved for an acquisition is frozen into its crawl provenance. Retention or
deletion policy remains separate and must not infer eligibility from these freshness settings.

## Query Surface

SQL over HTML is Atlas's bridge to downstream data and ML systems, not an attempt to implement
their feature pipelines. Atlas should expose stable, reusable structural operations over the
same underlying logic used internally:

- elements, children, descendants, and ancestors
- attribute lookup
- subtree/direct text
- links and URL resolution
- headings, images, forms, tables, and other structural projections
- bounded read-only SQL for advanced callers
- Arrow/Parquet export

The API and UI must enforce query time, memory, row-result, and scanned-data limits. Saved datasets,
feature engineering, semantic cleaning, embeddings, tokenization, and training remain external.

## Maintenance

DuckLake reduces custom file-management code but still requires scheduled repository maintenance:

- merge small adjacent files and apply current sort order
- expire snapshots outside configured retention, except pinned snapshots
- clean files no longer referenced after a safety delay
- reconcile object storage, ingestion state, and catalog state
- back up and restore the DuckLake catalog database

DuckLake owns physical filenames and rewrites. Atlas invokes and observes maintenance operations;
it does not implement a Parquet compactor. Initial analytical file targets should be benchmarked,
with roughly 128-512 MiB as a starting range rather than a fixed contract.

The initial production layout policy is access-path driven rather than retention-time driven:

- `documents` and `elements` are co-partitioned by a bounded hash bucket of `document_id`; elements
  are sorted by `(document_id, element_index)` and documents by `document_id`.
- `crawls` is bucketed and sorted for `(normalized_url, input_hash, captured_at)` cache lookups.
- `run_manifests` and `run_crawl_usages` are bucketed by `run_id`; usages are sorted by
  `(run_id, requested_url, usage_id)`.
- Calendar partitions are not used initially. Atlas retains historical data indefinitely by
  default, and day/month partitions would grow without bound while doing little for page-local,
  cache, and run-result access paths.

The starting bucket count is 1,024 for production-scale repositories. It is a deployment layout
setting, not part of the public DOM schema, and must be selected before bulk ingestion. Changing it
requires a managed rewrite. Repository maintenance merges small adjacent files within partitions,
applies the current sort order, and targets 256 MiB files (with 128-512 MiB accepted). It should run
when a partition accumulates at least eight files below 64 MiB and at least hourly while ingestion
is active. Exact thresholds must be calibrated by the repository benchmark at the intended corpus
and object-store latency; Atlas must expose the resulting file-count and compaction metrics.

`cd backend && uv run python -m catalogue benchmark --samples 100` runs the bounded read benchmark
against the configured repository. It measures set-based document resolution, cache lookup,
bounded element reads, and full page-local link projection. Run it on production-shaped data before
bulk ingestion, after changing layout/compaction settings, and as the corpus crosses each order of
magnitude. Benchmark results belong in deployment records rather than this architecture contract.

Raw HTML retention, DuckLake snapshot retention, operational event retention, staging cleanup, and
debug-media retention are separate policies. No single `MAX_HISTORY_AGE` may control all of them.

## Removed Postgres Crawl/Artifact Model

Atlas has no Postgres artifact, crawl, or observed-URL history tables.

Removed concepts map as follows:

| Current concept | Target concept |
| --- | --- |
| HTML `Artifact` | `documents` row + content-addressed `.html.zst` object |
| `Artifact.path` | relative `documents.html_object_key` |
| artifact SHA/size | document content metadata |
| artifact input hash | crawl input/provenance |
| HTML cache lookup | latest eligible DuckLake crawl -> document object |
| `dom.parquet` artifact | temporary ingestion/cache file -> durable `elements` rows |
| task-run artifact join | DuckLake run manifest + run/crawl usage association |
| artifact invalidation | crawl cache eligibility or repository retention/deletion |
| artifact API/UI | document and repository API/UI |

Generated query/data schemas reference source document/crawl identity. They do not retain legacy
artifact identifiers.

Screenshots, video, PDFs, and other optional media do not justify retaining the generic Postgres
artifact table. If Atlas gains durable secondary media, add a DuckLake `assets` table referencing
content-addressed object keys. Debug captures default to short independent retention.

## Public Result Semantics

Task-run list and status responses remain bounded. They never contain large result arrays or HTML.
A successful task result identifies durable repository state without embedding page bodies or
result arrays. `catalogue` is present only when `run_crawl_usages` contains durable data for that
run:

```json
{
  "version": 1,
  "primitive": "index",
  "status": "succeeded",
  "counts": {"pages": 100, "links": 4200},
  "catalogue": {"run_id": "..."}
}
```

The referenced manifest preserves the frozen task input, while `run_crawl_usages` resolves the
exact fresh or cache-reused crawls/documents used by the run. Policy-derived `no_store`, reused
schema operations that perform no crawl, and other runs without durable usage return
`"catalogue": null`.

Index traversal uses a temporary DuckDB workset for its frontier, visited state, and result counts;
it does not retain the site graph or page bodies in Python. Terminal run results contain bounded
counts and a catalogue run reference. Structural rows are queried from `elements` rather than
accumulated in Postgres or repeatedly embedded in polled run state. Large query results are
streamed or exported.

Planned document-oriented APIs replace the removed artifact-oriented APIs:

```text
GET /documents
GET /documents/{document_id}
GET /documents/{document_id}/html
GET /documents/{document_id}/elements
GET /crawls/{crawl_id}
```

Local filesystem paths are never part of public API contracts.

## Observability

Prometheus remains the operational metrics system. The repository must expose bounded metrics for:

- raw and compressed HTML bytes written/read
- document deduplication hits
- ingestion queue depth and oldest age
- ingestion duration, batch rows/bytes, retries, and failures
- DuckLake commit duration and snapshot ID progression
- catalog query duration, rows returned/scanned, timeout, and rejection
- file count/size distribution and compaction work
- snapshot expiration and file cleanup
- staging bytes and cleanup failures
- object-store latency/errors
- catalog backup/restore health

Identifiers, URLs, object keys, and document hashes must not become Prometheus labels.

## Remaining Migration Principles

The DOM/repository/DuckLake cutover, cache migration, document provenance migration, and removal of
Postgres crawl/artifact/URL history are complete. Remaining work must preserve that hard boundary:

1. Move task/effect/ingestion execution state to JetStream while retaining definitions in
   Postgres.
2. Remove Postgres task/effect run tables after all execution callers and foreign keys move.
3. Add the bounded document/crawl query and export APIs without exposing repository paths.

Hard cutovers are preferred over long-lived compatibility layers, but each cutover must leave the
repository and execution invariants testable. The irreversible DuckLake-only migration refuses to
drop populated legacy crawl/artifact tables unless the operator has stopped Atlas, backed up or
exported the control-plane database, and explicitly sets
`ATLAS_ALLOW_LEGACY_STORAGE_DROP=true`. Clean installations proceed without acknowledgement.

## Deliberately Open Decisions

- Exact captured-HTML semantics if Atlas later records original network response bytes.
- Benchmark-calibrated bucket count, microbatch thresholds, and compaction thresholds for each
  supported object store.
- Snapshot pinning and repository deletion UX.
- The first bounded SQL/query-builder API.
- Whether browser/crawl-capacity leases eventually move from Postgres to JetStream KV; this has not
  been decided by the storage design.
- Durable treatment of screenshots, video, PDF, and MHTML when a real caller requires it.
