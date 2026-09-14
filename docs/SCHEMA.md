# Schema

The canonical element replacement is specified in [ELEMENT_LAYOUT.md](ELEMENT_LAYOUT.md).

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

Physical contract version: `12.0.0`. The cutover resets disposable prior state; there is no graph-era
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

### `material.html_elements`

One row per `(content_sha256, node_index)` with parent_index, subtree_end_index,
sibling_index, depth, tag, namespace, attributes, text_direct, text, text_start and
text_end. IDs retain parsed preorder gaps. Parent references skip non-element
containers; the document element has null parent and depth zero. Sibling positions
count projected elements. The null-parent row proves content presence.

Every element stores exact descendant text in parsed order, including template
fragments and script/style/title text. No whitespace normalization or separators.
Internal code-point offsets support exact-text index candidate verification without storing text nodes. Elements use eight content buckets and sort by
content_sha256, node_index. Public html_element exposes text directly.

### `material.html_terms`

One row per `(term, content_sha256)` with sorted distinct `node_indexes INTEGER[]`.
ICU 77.1 / Unicode 16.0 root word boundaries segment original parsed page text once;
term keys use Unicode case folding followed by NFC. An element matches only when
it fully contains a page token's original code-point span. Enclosing ancestors
also match. Metadata attributes are excluded; title/script/style text follows the
canonical page stream. No stemming, substring matching or phrase positions.

Files use eight term buckets, term/content ordering and 2,048-row groups. Native
maintenance retains the same row-group setting and never aggregates posting arrays.
Postings are private physical data; there is no public term relation.
`search(terms VARCHAR[])` is a portable table macro over these postings.
It accepts at most 32 normalized term keys, matches any requested key, and returns
`content_id VARCHAR`, `node_indexes INTEGER[]` and `score DOUBLE`. Node indexes are
the sorted distinct union of matching containing elements; score counts distinct
requested keys matched per content. Duplicate/null/empty keys do not add score;
empty/null lists return no rows. Ranking and LIMIT belong to the caller. This is
word coverage, not frequency, phrase relevance or BM25.
Use exact normalized term keys; unnest `node_indexes` for individual element identities.

### `material.html_jsonld`

One row per `(content_sha256, node_index)` for an HTML JSON-LD script, storing
`value JSON` and `parse_error VARCHAR`. The shared parsed element context supplies
script text; a bounded standalone DuckDB connection preserves the public parser
contract. Invalid scripts remain rows. Files use eight content-hash buckets and
sort by `(content_sha256, node_index)`. This projection shares content ownership,
replay, rebuild and atomic activation with the other fixed projections.

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

Material batches write immutable Parquet and atomically append missing deterministic
identities after checking existing visit/content membership. Rebuilds create the complete hidden relation set and
activate it together. Postgres receipts make completed redelivery a no-op; native commit annotations
and membership checks make a lost receipt safe to replay. There is no head table or stored aggregate.

Each file under `materialization/projections/` declares one relation's ownership grain, identity,
Arrow and DuckLake schema, partitioning, sort order, projector, validation, and description. One
content-grain projection also declares the predicate that serves as the shared content-presence
marker. File discovery is the only material registry. Its digest is frozen into rebuild and
active-generation state and includes each projection file's implementation source; any add, edit,
or delete requires redeployment and a complete rebuild.

## Public catalogue: `public_v1` and `experimental`

Both independently declared schemas expose exactly six views: `page`, `capture`,
`link`, `html_element`, `html_metadata`, and `html_jsonld`, plus `search(terms)`.
`public_v1` is the default. Experimental definitions remain independently editable;
neither schema forwards to the other. The descriptions below apply to both.

Catalogue contract version: `3.0.0`. This directly replaces the previous surface:
`capture.page_url` replaces `requested_url`, `link.target_url` replaces `resolved_url`,
and request membership and specialized HTML extraction views are removed. There
are no aliases. Setup replaces both catalogues transactionally; no physical schema
change or materialization rebuild is required for this public-only change. Deploy
query services and clients with the matching catalogue. Existing saved SQL must be
updated directly. Readiness and request progress remain in operational APIs.

Query responses report the resolved `schema_version`; execution also reports
`source_snapshot`. Keys below describe logical identity, not enforced view keys.
A snapshot reference does not retain that snapshot indefinitely.

### `public_v1.page`

One row per normalized URL, with one non-null `url VARCHAR` column as its logical
key. It is the distinct union of requested and non-null effective capture URLs,
and link destinations from retained HTML captures. It contains uncaptured linked
destinations, but does not expose pending frontier work or acquisition failures
without HTML. URLs disappear when no retained public evidence references them.

Redirects, canonical declarations and identical bytes never merge URL identities.
A requested URL and a different effective URL are separate pages. Normalization
follows the ingestion URL rules above. This is a derived view, not a new page
store, mutable head, or persistence lifecycle.

### Deterministic HTML text

Every element stores all descendant parsed text in document order, including template
fragments, scripts, styles and titles. Whitespace is preserved; no separators are
inserted. Comments and attributes contribute no text. `text_direct` contains only
immediate child text; template fragments are not immediate text children. These
values describe the parsed document, not browser visibility or original byte spelling.
Use ordinary SQL predicates; corpus-wide text discovery scans text until a separate
acceleration layer is introduced. Use `search(terms VARCHAR[])` for exact page-word lookup.

### `public_v1.capture`

One acquisition with retained HTML (`lower(detected_media_type) = 'text/html'`).
Repeated captures can share content. HTTP error responses qualify when retained as
HTML; non-HTML documents and attempts without content do not appear. `capture_id` reuses the internal visit identity without a second lifecycle.

| Column | SQL type | Meaning |
| --- | --- | --- |
| capture_id | UUID | Acquisition identity |
| page_url | VARCHAR | Requested URL; references page.url |
| effective_url | VARCHAR | Final URL when known |
| captured_at | TIMESTAMPTZ | Capture time |
| http_status_code | INTEGER | Response status when known |
| content_id | VARCHAR | SHA-256 identity of retained logical bytes, non-null |
| byte_length | BIGINT | Length of retained logical bytes before storage compression, non-null |
| encoding | VARCHAR | Detected character encoding when meaningful, otherwise null |

Media type and representation remain private acquisition evidence. Captured HTML
may reflect browser rendering and is not necessarily the original HTTP response
body. content_id identifies the exact retained logical bytes used by the HTML
projection. HTML captures remain visible before their projections are ready.
XHTML and other document formats are not currently included. The public content
download route resolves only retained HTML; private document access is unchanged.

`GET /api/content/{content_id}` on the public app retrieves the original logical
bytes as an attachment. Storage compression and repository keys are private.
There is no public object relation. Raw bytes remain content-addressed and shared
across captures; node and element storage remains content-owned, hash-partitioned
and sorted by content identity and node position. Adding capture byte metadata
requires no physical rewrite or materialization rebuild.

### `public_v1.html_element`

One row per `(content_id VARCHAR, node_index INTEGER)` with parent_index,
subtree_end_index, sibling_index and depth (INTEGER), tag and namespace (VARCHAR),
attributes (MAP(VARCHAR,VARCHAR)), text_direct and text (VARCHAR).

Positions preserve Lexbor preorder IDs, with gaps for non-element nodes. Parent
references identify the nearest enclosing element; the document element has no
parent and depth zero. Sibling positions count elements only. Subtree end is
exclusive in the same preorder coordinate space. Template fragments participate
in traversal but are not stored as rows. Attribute namespaces use Clark notation.

Lexbor 3.1.0 through Selectolax 0.4.11 determines HTML5 tree repair and names.
Text, comments and fragment nodes exist transiently while parsing. Their separate
identities are not persisted. Raw bytes preserve source spelling and comments.
Rendered Unicode is used directly; response bytes use BOM, supported early meta
charset, then Windows-1252, replacing malformed byte sequences deterministically.
A parser change requires rebuilding all dependent projections together.

HTML materialization is asynchronous. A missing document element can mean pending
materialization. Use LEFT JOIN when retaining captures without structure matters.

### `public_v1.html_metadata`

A content-owned view of explicit HTML declarations, computed from existing
primitives without new storage or materialization. Columns, in order:
`content_id VARCHAR`, `node_index INTEGER`, `kind VARCHAR`, `name VARCHAR`,
`value VARCHAR`. The key is `(content_id, node_index, kind, name)`.

| kind | Source | name | value |
| --- | --- | --- | --- |
| title | HTML title element | title | Ordered descendant text |
| meta_name | meta with name | name attribute | content attribute |
| meta_property | meta with property | property attribute | content attribute |
| meta_http_equiv | meta with http-equiv | http-equiv attribute | content attribute |
| meta_charset | meta with charset | charset | charset attribute |
| link_rel | link with rel | Each distinct declared relation token | href attribute |
| html_attribute | html with lang | lang | lang attribute |

Only HTML-namespace elements are included. Declarations are not restricted to
head; the source tree remains available for callers requiring that constraint.
An element with multiple declaration attributes emits each applicable kind.
Repeated declarations on different nodes remain separate rows, with no preferred
value or conflict resolution. Names and values retain their parsed spelling,
case and whitespace. HTML parsing has already decoded character references;
exact source serialization remains in raw bytes. Link rel splits on HTML ASCII
whitespace and deduplicates identical tokens within that element, retaining token
case. Original rel spelling and ordering remain in html_element.attributes.

Missing content/href yields null; an explicitly empty attribute yields an empty
string. An empty title yields an empty string. Title text concatenates descendant
text without trimming or inserted separators. The HTML5 parser handles HTML titles
as RCDATA, so their stored `html_element.text_direct` supplies that same text.
Relative URLs remain relative.
The language and charset are declarations, not detection results; http-equiv is
not evidence of a received HTTP header. No metadata inference, URL resolution,
JSON-LD parsing, or body-level microdata extraction is performed.

```sql
SELECT node_index, kind, name, value
FROM public_v1.html_metadata
WHERE content_id = ?
ORDER BY node_index, kind, name;
```

### `public_v1.html_jsonld`

A content-owned view over `material.html_jsonld`, with one row per HTML script
whose declared type has the application/ld+json media-type essence. Type matching
ignores ASCII case, surrounding HTML ASCII whitespace and semicolon parameters.
The key is `(content_id, node_index)`. Parsing happens during materialization.

| Column | SQL type | Meaning |
| --- | --- | --- |
| content_id | VARCHAR | Source content identity |
| node_index | INTEGER | Source script node |
| value | JSON | Complete parsed document; SQL null on failure |
| parse_error | VARCHAR | Null on success; otherwise a stable parse error |

Ordered immediate script text comes from `html_element.text_direct` and is parsed
during materialization using DuckDB's JSON parser and its accepted syntax. Queries
read the stored records without scanning general HTML elements.
Empty/whitespace-only scripts report `Empty JSON-LD script`; other parser failures
report `Invalid JSON syntax`. Invalid declarations remain rows rather than failing
the query. A valid JSON null is JSON `null`, with no parse error, distinct from
SQL null on failure. Arrays, objects, @graph and scalar JSON values remain complete;
this is syntax parsing, not JSON-LD semantic validation. Duplicate scripts remain
separate source rows. No entity flattening, context fetching, URL resolution, RDF
expansion, or schema.org interpretation occurs. Script src URLs are not fetched;
a source-only script with no inline text is reported as empty.

Parsed script text is available from html_element.text_direct through the source
node. HTML script raw-text parsing does not decode entity-like strings such as
`&amp;`. Filters on content_id/node_index restrict source selection.

```sql
SELECT node_index, value ->> '@type' AS declared_type, parse_error
FROM public_v1.html_jsonld
WHERE content_id = ?
ORDER BY node_index;
```
### `public_v1.link`

Columns in order: `capture_id UUID`, `node_index INTEGER`, `target_url VARCHAR`,
`raw_href VARCHAR`. One resolvable HTTP(S) anchor occurrence per
`(capture_id, node_index)`. `capture_id` references the source capture and
`target_url` references `page.url`. Source page identity is `capture.page_url`;
relative href resolution uses the effective URL and applicable document base URL.
`raw_href` preserves the original parsed attribute. Repeated anchors remain separate.

Join capture first to obtain content identity before joining the source element.
A destination need not have been captured. Links describe that capture's version;
choose captures explicitly to construct a graph at the desired time. There is no
implicit latest-capture or successful-status filter.

### `search(terms VARCHAR[])`

Both schemas expose the same portable table macro. Supply at most 32 Unicode
case-folded, NFC-normalized word keys. Inputs are exact word keys, not a free-text
query parser. Matching is any-word over parsed page text, including title,
script and style text; attributes and comments are excluded.

| Column | SQL type | Meaning |
| --- | --- | --- |
| content_id | VARCHAR | Content matching at least one requested key; one row per content |
| node_indexes | INTEGER[] | Sorted distinct elements fully containing any matched page word |
| score | DOUBLE | Number of distinct requested keys found in the content |

Null, empty and duplicate keys add no score. Empty/null lists return no rows.
An element in the union need not contain all matched keys. Words crossing inline
markup belong only to elements containing the complete word. No stemming,
phrase matching, frequency weighting or readable-text extraction is implied.
Join capture on content_id for page URLs and capture provenance; repeated captures
intentionally produce multiple rows. Choose ordering and limits explicitly:

```sql
SELECT c.page_url, c.capture_id, c.captured_at, s.score
FROM public_v1.search(['robot', 'science']) s
JOIN public_v1.capture c USING (content_id)
ORDER BY s.score DESC, c.page_url, c.capture_id
LIMIT 20;
```

The term index is private. Equality, LIKE and ILIKE on html_element.text retain
ordinary DuckDB semantics; search does not redefine those predicates. The existing
experimental exact-text candidate optimization reads the private index and keeps
the original equality check. Stable retains native SQL execution.

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
dispatch-policy version and frozen timeout/measured milliseconds. Final start freezes this domain
snapshot after checking current policy; edits do not rewrite already-started attempt evidence.
Unknown attempts retain the same frozen policy provenance and frozen timeout bound. Nested policy values use canonical JSON in DuckLake.


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

Evidence is append-only while retained. The janitor can retire collection and observation
rows and their projections. Control Postgres owns `lake_write_claims`, `retired_evidence`
and `retention_objects`; it also owns `materialization_state` and
`materialization_applied_batches`. No Periplus operational data tables live in DuckLake.
The lake contains evidence and derived dataset generations. Native catalogue metadata
and the CDC cursor remain DuckLake-owned. See [RETENTION.md](RETENTION.md) for claims,
episode identities, expiry and snapshot/reader guarantees.

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

Current `frontier_interests` may outlive their acquisition payload while the parent request
continues. After dependencies are complete and evidence committed, `acquisition_id` and
selection context become null. `completed_status` and `completed_evidence` preserve current
request progress; URL keys still enforce request-local deduplication. These compact rows
are operational state, not a historical corpus, and retire with the parent request.


## Experimental catalogue: `experimental`

The separate experimental query endpoint selects only `experimental.*`. Its views and
`search()` macro are independently declared over the same physical evidence as Stable.
The initial columns match public_v1; subsequent experiments may change them freely.
The endpoint's discovery response is authoritative for its current relation names and columns.
A successful experiment is released as a new `public_vN` contract; released views never
alias mutable experimental views. See [QUERY.md](QUERY.md#stable-and-experimental-execution).
