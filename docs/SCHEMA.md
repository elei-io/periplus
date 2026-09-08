# Schema

Periplus has one evidence path:

```text
immutable objects + ingest.*
    -> append-only material.*
    -> runtime public_v1.*
```

Raw bytes and both physical schemas are immutable. The public catalogue exposes a deliberately
small evidence kernel.

Periplus owns observation faithfully. Interpretation begins outside Periplus. It does not publish a
`data.*` schema or define domain entities such as companies, products, people, claims, or topics.

Physical contract version: `10.0.0`. The cutover resets disposable prior state; there is no graph-era
crawl table or compatibility migration.

## `ingest.*`

The authoritative relations are:

- `ingest.visits` — one terminal native acquisition of a URL.
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

The fixed projection registry is authoritative. The registry declares the following document structures plus visit readiness:

### `material.html_nodes`

One row per `(content_sha256, node_index)` containing the complete parsed document
node tree. It shares the parse context and position space with elements and links.
The `node_index = 0` document row is the generation content-presence marker.

### `material.html_elements`

One row per `(content_sha256, element_index)`:

```text
content_sha256, element_index
parent_index, subtree_end_index, depth, child_index
tag, namespace, attributes
text_direct, text_tail
```

The private `element_index` column uses the complete node position space: element
positions may have gaps. `child_index` counts all sibling nodes. `text_direct`
concatenates immediate text children; the private `text_tail` field is empty.
Rows are depth-first. `subtree_end_index` is exclusive. The document root in `material.html_nodes` is the content-projection
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

## Public catalogue: `public_v1`

The single public namespace is `public_v1`. Query API requests default to this
version, so `SELECT * FROM capture` and `SELECT * FROM public_v1.capture` are
identical. Explicit unsupported `schema_version` values are rejected. Preparation
and execution report `schema_version`; execution additionally reports
`source_snapshot`. A schema version specifies semantics, not a data snapshot or a
promise that an expired snapshot can be replayed. Physical layout is private.
There are no `web` or `content` compatibility namespaces.

### `public_v1.capture`

One acquisition with retained content. Repeated captures can share content. HTTP
error responses qualify if their bodies were retained; attempts without content do
not appear. `capture_id` reuses the internal visit identity without a second lifecycle.

| Column | SQL type | Meaning |
| --- | --- | --- |
| capture_id | UUID | Acquisition identity |
| requested_url | VARCHAR | Normalized requested URL |
| effective_url | VARCHAR | Final URL when known |
| captured_at | TIMESTAMPTZ | Capture time |
| http_status_code | INTEGER | Response status when known |
| content_id | VARCHAR | SHA-256 identity of retained logical bytes, non-null |
| byte_length | BIGINT | Length of retained logical bytes before storage compression, non-null |
| representation | VARCHAR | `response_body` or `rendered_html` |
| media_type | VARCHAR | Detected media type |
| encoding | VARCHAR | Detected character encoding when meaningful, otherwise null |
| request_ids | UUID[] | Sorted unique coverage request IDs, non-null; empty when no membership evidence is visible |

`GET /api/content/{content_id}` on the public app retrieves the original logical
bytes as an attachment. Storage compression and repository keys are private.
There is no public object relation. Raw bytes remain content-addressed and shared
across captures; node and element storage remains content-owned, hash-partitioned
and sorted by content identity and node position. Adding capture byte metadata
requires no physical rewrite or materialization rebuild.

### `public_v1.html_node` and `public_v1.html_element`

Both relations share `content_id VARCHAR`, `node_index INTEGER`,
`parent_index INTEGER`, `subtree_end_index INTEGER`, `sibling_index INTEGER`.
Identity is `(content_id, node_index)` within the returned catalogue snapshot.
Positions are zero-based depth-first positions across **all** nodes, including the
document root. Subtree end is exclusive. Parent is null only for the document
root. Sibling positions count all node kinds.

`html_node` adds `node_type VARCHAR`, `name VARCHAR`, `namespace VARCHAR`, and
`value VARCHAR`. Kinds are `document`, `doctype`, `element`, `text`, `comment`, and
`processing_instruction` (where produced by HTML5 parsing). Name is the local
name for elements/doctypes or instruction target; otherwise null. Namespace is a
URI where applicable, otherwise null. Value contains text/comment/instruction
content; other kinds have null values. Doctype source details remain in raw bytes.

`html_element` adds `tag VARCHAR`, `namespace VARCHAR`,
`attributes MAP(VARCHAR, VARCHAR)`, and `text_direct VARCHAR`. Attribute keys use
Clark notation `{namespace-uri}local-name` for namespaced attributes; other keys
are unchanged local names. Direct text concatenates immediate child text nodes in
order, including text after child elements. It excludes descendant element text.
Empty direct text is an empty string. Neither relation models CSS visibility.

The projection describes an HTML5 parsed tree, including parser-inserted elements,
not source token offsets. Exact spelling, duplicate source attributes, entity
spelling and other serialization details remain in original bytes. Adjacent text
fragments are merged. A parser change requires a complete coherent generation;
node references must not be reused across snapshots without checking identity.

HTML projections are asynchronous. A capture with no matching node root can be
non-HTML or awaiting materialization; absence does not prove an empty document.
HTML readiness uses the content document-root presence marker. Use LEFT JOIN when
retaining captures without available structure matters.

### `public_v1.link_occurrence`

`capture_id UUID`, `node_index INTEGER`, `raw_href VARCHAR`, `resolved_url VARCHAR`.
One resolvable HTTP(S) anchor occurrence per `(capture_id, node_index)`. Original
parsed href values are retained; targets use existing URL normalization. Resolution
uses the capture's effective URL and applicable document base URL. Join capture
first to obtain content identity before joining the source element. A linked
destination need not have been captured.

### `public_v1.subtree_text`

A table macro accepts `source_content_id VARCHAR`, `root_node_index INTEGER`,
optional `max_chars` (default 20000, range 0–100000) and `max_nodes` (default 10000,
range 1–10000). It returns `text VARCHAR`, `truncated BOOLEAN`, `total_chars BIGINT`,
and `node_count BIGINT`. Text nodes within the subtree are concatenated in order,
without separators, trimming or visibility filtering. Comments are excluded.
Script/style text is included. Missing roots return zero rows; empty existing
subtrees return one empty result. Character truncation is explicit. Oversized
subtrees fail rather than returning incomplete text without notice.

### Collection lineage

`public_v1.capture.request_ids UUID[]` contains the sorted, unique coverage request
IDs supplied with each capture, including shared and reused captures. Capture keeps
one row per capture regardless of how many requests use it. The IDs are the collection
UUIDs returned by the control API. Filter with
`list_contains(request_ids, CAST(? AS UUID))`.

The list is empty when no membership evidence is visible. Membership arrives through
asynchronous evidence ingestion and can grow when later requests reuse a capture.
It does not imply that a request or materialization is complete. Acquisitions without
retained content remain excluded from capture. Request membership is derived from
immutable fulfillment evidence; it adds no persistence or materialization path.

Collection specifications, progress, acquisition modes, and dispatch reasons are
available through the collection/operational APIs, not public SQL tables. Internal
lineage evidence and its retention remain unchanged.

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
Unknown attempts retain the same frozen policy provenance and conservative time charge. Nested policy values use canonical JSON in DuckLake.


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

### Operator inspection of capture conditions

The capture snapshot can be compared without operational state or a policy-history table:

```sql
SELECT visit_id, requested_url, observed_at,
       capture_policy->>'slug' AS policy_rule,
       capture_policy->'content'->'completion'->'scroll'->>'enabled' AS scroll_enabled,
       capture_policy->'content_variance' AS policy_variance
FROM ingest.visits
WHERE requested_url = 'https://example.com/'
ORDER BY observed_at DESC
LIMIT 100;
```

Contract 9 requires a frozen policy for native evidence, including terminal failures and
cancellations; it does not invent policies for observations already recorded without one.
Deployment requires a coordinated catalogue cutover from the previous contract.

Local capture-provenance cutover completed on 2026-09-08: physical contract 9.0.0 and
public catalogue 3.0.0 were installed before the public_v1 replacement. The previous 587-observation development lake
was backed up with all five stopped persistent volumes before reset. Editable content
and domain policies, public access configuration, and crawler controls were restored;
execution counters and quota windows started fresh. All 18 Compose services passed
health checks. A depth-zero, one-page request captured and ingested example.com,
completed materialization, and returned its policy through both direct query-service
and public-web SQL. Request `ffecb5d8-0b4a-4103-9100-bf9e610bdcb0` is query-ready.
