# Schema

Periplus has one evidence path:

```text
immutable objects + ingest.*
    -> append-only material.*
    -> runtime web.* / content.*
```

Raw bytes and both physical schemas are immutable. The public catalogue exposes a deliberately
small evidence kernel.

Periplus owns observation faithfully. Interpretation begins outside Periplus. It does not publish a
`data.*` schema or define domain entities such as companies, products, people, claims, or topics.

Physical contract version: `8.0.0`. The cutover resets disposable prior state; there is no graph-era
crawl table or compatibility migration.

## `ingest.*`

The authoritative relations are:

- `ingest.visits` — one terminal acquisition or imported observation of a URL.
- `ingest.attempts` — ordered acquisition attempts for a visit.
- `ingest.steps` — content-completion actions within an attempt.
- `ingest.documents` — the optional immutable content retained by a visit.
- `ingest.collections` — frozen finite collection definitions.
- `ingest.collection_outcomes` — separately appended terminal request outcomes.
- `ingest.fulfillments` — one request URL result associated with an existing observation.
- `ingest.acquisition_reasons` — request causal reasons frozen at dispatch.

They contain observed evidence only. There is no page dimension, URL decomposition, latest-state
pointer, or materialization hint in ingestion. Inserts are idempotent only when an existing
identity has identical evidence; conflicting reuse fails. No update, correction, replacement, or
deletion path exists.

An acquisition attempt may have outcome `uncertain` after loss of its executing worker. Its
`finished_at` is null when the actual finish time is unknown. Recovery does not invent a finish
time or describe a repeated remote attempt as exactly-once execution.

Requested and effective URLs are normalized before their frozen ingestion job is produced:
surrounding whitespace and fragments are removed, scheme and hostname are lower-cased, default
ports are removed, an empty path becomes `/`, and the query string is retained byte-for-byte.
Only absolute HTTP(S) URLs without credentials are accepted.

One visit retains zero or one content representation. Zero covers acquisition failures, empty
responses, and responses Periplus deliberately does not retain. Otherwise the representation is the
single result returned by that visit and may be HTML, JSON, PDF, an image, text, XML, or unsupported
binary content. A visit never owns a collection of alternate renderings or derived artifacts.

An ingestion document records its internal identity, visit identity, representation and media
metadata, `content_sha256`, logical and stored sizes, storage encoding, and a repository-relative
object key. Every visible document reference must resolve to readable immutable bytes whose
logical SHA-256 matches `content_sha256`. Internal document identities and object keys are not part
of the public SQL contract.

## `material.*`

The fixed projection registry is authoritative. Exactly three semantic relations exist:

### `material.html_elements`

One row per `(content_sha256, element_index)`:

```text
content_sha256, element_index
parent_index, subtree_end_index, depth, child_index
tag, namespace, attributes
text_direct, text_tail
```

Rows are depth-first. `subtree_end_index` is exclusive. Element zero is the content-projection
presence marker; no content manifest or statistics row is maintained. That marker permanently
prevents later visits from re-emitting content-grain HTML or JSON-LD rows, while a deterministic
minimum document identity selects one owner when genuinely new content first appears in parallel
batches.

### `material.jsonld_values`

One successfully parsed JSON-LD script per `(content_sha256, element_index)`:

```text
content_sha256, element_index, type_terms, value
```

### `material.link_occurrences`

One visit-owned anchor observation per deterministic `occurrence_id`:

```text
occurrence_id, link_id
visit_id, document_id, content_sha256, element_index
observed_at, raw_href
source_url, target_url, relation_scope
```

`occurrence_id` derives from `(document_id, element_index)`. `link_id` is a deterministic
convenience for the normalized directed URL pair. It is not a mutable identity record.
`relation_scope` is `self`, `same_origin`, `same_host`, `same_site`, or `external`.

Material relations accept immutable Parquet-file appends only. Rebuilds create the complete
discovered hidden relation set and activate it together. Live batches register new final files.
There is no `MERGE`, `UPDATE`, `DELETE`, keyed replacement, head table, or stored aggregate.

Each file under `materialization/projections/` declares one relation's ownership grain, identity,
Arrow and DuckLake schema, partitioning, sort order, projector, validation, and description. One
content-grain projection also declares the predicate that serves as the shared content-presence
marker. File discovery is the only material registry. Its digest is frozen into rebuild and
active-generation state and includes each projection file's implementation source; any add, edit,
or delete requires redeployment and a complete rebuild.

## Public catalogue

The public catalogue exposes observation/content evidence and separate collection lineage:

```text
web.observation
web.link_occurrence
web.collection
web.fulfillment
web.acquisition_reason
content.object
content.html_element
```

Only `web.*` and `content.*` are public. The `web` schema contains observation-contextual evidence;
the `content` schema contains content-addressed objects and deterministic structures derived from
their bytes. Their views use a separate lightweight registry and are not materialization
declarations.

The canonical fully qualified form uses the attached catalogue name:

```text
periplus.web.observation
periplus.web.link_occurrence
periplus.content.object
periplus.content.html_element
```

When Periplus is the current catalogue, callers may use the shorter two-part names. Clients combining
Periplus with their own attached databases should use the fully qualified form.

### `web.observation`

One terminal URL observation, including unsuccessful observations:

```text
observation_id
requested_url, effective_url
observed_at, outcome, http_status_code
content_id
source_kind, source_system, source_dataset, source_record_id
```

`content_id` is nullable and identifies the one retained content object when present. The relation
has no owning crawl or collection: one acquisition can supply multiple collections. Collection
fulfillment and acquisition reasons belong to separate lineage relations in the frontier cutover.
The relation
does not implicitly join URL components, current or latest state, acquisition attempts, content
statistics, or parsed structures.

### `content.object`

One immutable byte sequence retained by at least one observation:

```text
content_id
size_bytes
detected_media_type
detected_character_encoding
content_format
```

`content_format` is the detected representation class used to select deterministic format
projections. A content object remains visible even when Periplus has no public structural projection
for its format. The relation exposes neither repository keys nor physical storage paths.

Public `content_id` values are the same SHA-256 content identities stored physically as
`content_sha256`.

The same `content_id` may belong to observations of multiple URLs, collections, sources, or times.
Content-grain projections are therefore emitted once and reused through observation joins.

### `content.html_element`

One structural HTML element per `(content_id, element_index)`:

```text
content_id, element_index
parent_index, subtree_end_index, depth, child_index
tag, namespace, attributes
text_direct, text_tail
```

It exposes the deterministic HTML5 projection of objects whose `content_format` is `html`.
Document order, parentage, subtree bounds, attributes, and text placement are explicit. Objects of
other formats have no rows in this relation.

### `web.link_occurrence`

One observed anchor occurrence in one observation:

```text
link_occurrence_id
observation_id, content_id, element_index
observed_at
source_url, raw_href, target_url, relation_scope
```

The observation grain is required because resolving `raw_href` depends on the effective source URL
even when identical content bytes appear at multiple URLs. `target_url` is the normalized resolved
HTTP(S) target. `relation_scope` is `self`, `same_origin`, `same_host`, `same_site`, or `external`.

### `content.subtree_text`

A public table macro reads one immutable HTML subtree by `source_content_id` and
`root_element_index`. Optional `max_chars` (default 20,000; allowed 0–100,000) bounds output
characters. `max_elements` (default and maximum 10,000) rejects oversized subtrees.
It returns `text`, `truncated`, `total_chars`, and `element_count`.

Text follows DOM document order, preserving existing whitespace and including descendant
text tails only after their subtrees. The selected root's tail is excluded. Missing roots
return zero rows; empty roots return empty text. There is no CSS visibility filtering,
inserted block separator, whitespace normalization, deduplication, or summarization.
This is faithful projected DOM text, not browser-rendered text or original HTML bytes.

Helper declarations, SQL resources, documentation and examples are owned by the explicit
registry in `platform/catalogue/helpers/`. Installation uses the same setup transaction
as the public views. No new materialized relation is introduced.

### Collection lineage

`web.collection` exposes public frozen intent and its optional terminal outcome. It has one row per
collection; an absent outcome can mean collection is active or outcome ingestion is pending.
`seed_provenance` in a collection outcome retains the seed query snapshot, query ID, selection
time, and frozen-candidate digest. It remains available after control-state cleanup; SQL and
parameters remain in the frozen collection specification.
`web.fulfillment` has one row per request URL result and references the independently owned
`observation_id`. Different collections may reference the same observation, including later recent
reuse. `web.acquisition_reason` records the collection reason frozen at dispatch;
later reuse does not retroactively become a cause of capture. Every lineage view exposes shared evidence from all request classes. Observation queries do not implicitly join any lineage view or multiply their rows.

Every acquisition reason requires a collection ID. Scheduled system work uses the same collection
lineage as other requests. The parent observation and selection rule identify the discovery cause.
Background-specific selection provenance is removed. Request expiration records `duration_limit`;
execution deadlines remain derived runtime timestamps, not a separate terminal outcome.
These fields survive operational selection-check expiry through the ordinary lineage ingestion lane.

Lineage jobs use the ordinary ingestion lane with stable identities and conflicting-evidence
rejection. They append independently of visit insertion and never trigger visit materialization.
During asynchronous ingestion a relationship may precede its referenced evidence. Query readiness
must therefore be checked separately, not inferred from a lineage row's existence.

`material.visit_readiness` records each visit processed by a materialization batch, including
visits with no applicable structural output. Its files commit with the other projection files and
applied-batch marker, and participate in the same atomic generation activation. It is a derived
membership proof, not another ingestion cursor. For HTML, readiness additionally requires the
active content-presence marker: a separate visit batch can own the shared DOM/JSON-LD output.

Arrivals, Live recent captures, and current frontier-item drilldowns report `query_ready: true` only when visible base evidence,
visit membership, applicable content presence, and the matching active registry are verified in
one statement snapshot. `false` means materialization is pending in the verified active generation;
`null` means readiness could not be verified, with an explicit reason. This describes the observed
generation, not a promise that a later query cannot fail or that a future rebuild has finished.

Collection API detail distinguishes `source: current` from `source: history`. Historical detail
reads immutable definition/outcome rows, preserves unknown counters when the outcome has not
arrived, and does not infer materialization readiness. The current-state list explicitly identifies
its scope. A supplied collection ID is checked against immutable history when absent from current
state; identical intent returns that historical request, while conflicting intent cannot restart it.
History unavailability defers that identity check rather than treating failure as absence.

During retirement, current collection rows remain hidden while their dependent interests and outbox
rows are pruned in bounded batches. Definition/outcome receipts, all associated lineage receipts,
and accepted evidence receipts must be durable before the handoff begins. API detail continues from
history throughout pruning. Private history is available only through authorized administrative
detail reads; all retained evidence remains shared.

### Attempt control provenance

Frontier attempt `resource_usage` retains the effective domain policy snapshot (identity, version,
actor, concurrency, interval, and pause setting) and exclusion-policy version alongside the global
physical-allowance version and reserved/measured milliseconds. Final start freezes this domain
snapshot after checking current policy; edits do not rewrite already-started attempt evidence.
Unknown attempts retain the same frozen policy provenance and conservative time charge. Imported
attempts may omit frontier control provenance. Nested policy values use canonical JSON in DuckLake.


Current Postgres collection state retains an optional `admission_timing` observation containing the
crawler control version, request priority, and first committed admission timestamp. It is written
in the same transaction as the first interest and never reset by admission replay. Changing request
priority invalidates comparability; changing crawler controls before first admission also invalidates
the sample. This bounded operational observation retires with the collection and is not another
crawl-history table. Alembic revision `20260907_0002` adds the nullable observation column; missing
observations remain unknown and are not reconstructed from historical data.

The nullable operational `collections.last_progress_at` timestamp is updated transactionally on
execution progress, independently of scheduler claims and view polling. Current views also consider
stored submission, last dispatch and completion timestamps; they never use read time as progress.
It is not a crawl-history relation and is reclaimed with the collection.

Completion-step `parameters` contains the executed `action_version` and `config` plus a distinct
`measurements` object with iterations and before/after element, text, link and scroll-height counts.
These measured completion signals accompany duration and stopping reason; they do not certify
usefulness or completeness. A successful observation means capture checks passed, and may still
contain a challenge or incomplete content. Immutable source bytes support later quality evaluation.

## Retention lifecycle

Evidence is append-only while retained. The janitor can explicitly retire request and
observation evidence and its owned projections. `material._periplus_retention_identities`
holds exact transaction fences and replay receipts; `material._periplus_retention_objects`
holds pending raw-object retirements. These internal tables are mutable bookkeeping,
not public corpus relations. See [RETENTION.md](RETENTION.md) for each column, ownership,
expiry semantics and the snapshot/reader guarantees.

## Request schedule controls

`request_definitions` stores versioned reusable intent. `request_schedules` stores
cadence, bounds and current execution controls. Schedule origin is frozen inside
the existing `ingest.collections.specification` JSON; no history mirror is added.
See [SCHEDULES.md](SCHEDULES.md). Background-specific frontier fields and the
background-check table are removed by Alembic revision `20260908_0005`.

Reusable intent accepts `max_duration_seconds`; frozen execution intent additionally
contains the server-derived `deadline_at`. Both remain inside existing specification
JSON. Revision `20260908_0006` removes the previously mandatory-null deadline key from
reusable definitions; recorded execution deadlines and immutable evidence are retained.

### Private query execution history

[QUERY_HISTORY.md](QUERY_HISTORY.md) defines the 30-day private `query_executions`
table in control Postgres, bounded best-effort recording, janitor cleanup and the
`observatory/queries` dashboard. This is explicitly approved product analytics;
no query results or crawl history are added to control Postgres.
