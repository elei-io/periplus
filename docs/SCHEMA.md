# Schema

Atlas separates observed evidence, Atlas-maintained derivations, the public query interface, and
user-owned data.

## Type policy

Atlas uses the narrowest native DuckDB type that faithfully represents a value:

- Scalars, `STRUCT`, `LIST`, and `MAP` for known shapes.
- `VARIANT` for heterogeneous or evolving semi-structured values.
- `JSON` only when textual JSON is itself the required representation.

Immutable source bytes remain in object storage rather than being copied into the catalogue.

## `ingest.*`

Append-only records committed by native acquisition or evidence import:

- `ingest.crawls` — bounded executions of crawl plans.
- `ingest.visits` — destinations observed during a crawl.
- `ingest.attempts` — acquisition attempts made for a visit.
- `ingest.steps` — ordered content-completion executions within an attempt.
- `ingest.documents` — references to immutable document bytes in object storage.

### `ingest.crawls`

One row is written when a crawl reaches its terminal state. Current execution state is not part of
the ingestion schema.

```text
crawl_id           # Unique identity of this crawl execution.
kind               # atlas or import.
graph_id           # Stable logical identity of the crawl plan; null for imports.
graph_config_hash  # Hash of the canonical frozen graph configuration.
graph_config       # Complete frozen graph configuration as VARIANT.
root_url_count     # One for a native crawl whose root was admitted; zero otherwise.
started_at         # Time at which crawl execution began.
finished_at        # Time at which crawl execution stopped.
stop_reason        # Reason the crawl stopped.
```

### `ingest.visits`

```text
visit_id          # Unique identity of this visit.
crawl_id          # Crawl that produced the visit.

requested_url     # Exact URL Atlas attempted to visit.
effective_url     # Final URL after navigation or redirects; null if never resolved.

admitted_at       # Time the destination entered the crawl.
started_at        # Time acquisition began.
observed_at       # Time the returned document was captured; null if none.
finished_at       # Time the visit reached its terminal outcome.

outcome           # Final logical result: succeeded, failed, cancelled, or another terminal state.
status_code       # Final HTTP status when available.
document_id       # Document produced by the visit; null if none.
```

Traversal and admission paths are operational state and are not retained here. The durable web
graph is derived from observed document content into `material.links`.

### `ingest.attempts`

```text
attempt_id       # Unique identity of this acquisition attempt.
visit_id         # Visit this attempt belongs to.
attempt_index    # Order of this attempt within the visit.

started_at       # Time acquisition work began.
finished_at      # Time the attempt reached its terminal outcome.

effective_url    # Final URL reached by this attempt; null if unresolved.
status_code      # HTTP status observed by this attempt; null if unavailable.

outcome          # Terminal result: succeeded, failed, or cancelled.
failure_stage    # Stage that failed; null on success.
failure_code     # Stable machine-readable failure reason; null on success.
failure_message  # Bounded diagnostic detail; null when unnecessary.
```

### `ingest.steps`

```text
attempt_id             # Attempt this step belongs to.
step_index             # Execution order within the attempt.

action                 # Controlled action name, such as wait_dynamic, wait_fixed, scroll, or expand.
parameters             # Complete frozen parameters for this execution as VARIANT.

started_at             # Time execution began.
duration_ms            # Total execution duration in milliseconds.
outcome                # Execution result: succeeded, failed, or cancelled.
stopping_reason        # Why execution stopped; null when not applicable.

error_code             # Stable machine-readable failure reason; null on success.
error_message          # Bounded diagnostic detail; null when unnecessary.
```

### `ingest.documents`

A visit produces zero or one authoritative document. A document belongs to exactly one visit.

```text
document_id      # Unique identity of this document observation.
visit_id         # Visit that produced the document.
attempt_id       # Successful attempt; null when an import did not retain one.

observed_at      # Time the document representation was captured.
representation   # Meaning of the bytes, such as response_body or rendered_html.

declared_media_type # Media type claimed by the source; null when unavailable.
detected_media_type # Media type determined by Atlas from the acquired representation.
charset             # Character encoding when meaningful; otherwise null.

content_sha256   # Hash of the uncompressed logical document bytes.
content_bytes    # Size of the uncompressed logical document bytes.

object_key       # Repository-relative pointer to the immutable bytes.
storage_encoding # Encoding used for the stored bytes.
stored_bytes     # Size of the stored object.
```

Imported visits retain typed provenance. Native observations use `atlas`; imports retain their
source system, optional dataset, and source record identity. The physical column is a native
`STRUCT(kind, system, dataset, source_record_id)`, not semi-structured JSON or `VARIANT`.

## `material.*`

Versioned, rebuildable relations maintained by Atlas:

- `material.html_elements` — structural projections of HTML documents.
- `material.jsonld_values` — structured data extracted from JSON-LD embedded in HTML documents.
- `material.pages` — visits reduced to unique page identities.
- `material.page_observations` — normalized pages connected to their visit and document evidence.
- `material.links` — stable normalized source-target page pairs.
- `material.link_observations` — document-owned anchor evidence for those pairs.

Structural projections for generic JSON, XML, PDF, DOCX, CSV, and other formats are deferred. Their
relation names and schemas are not yet part of the contract.

### `material.html_elements`

```text
content_sha256    # Identity of the projected immutable HTML bytes.
element_index     # Zero-based element position in depth-first document order.

parent_index      # Parent element index; null for the root.
subtree_end_index # Exclusive end of this element's subtree in document order.
depth             # Element depth from the root.
child_index       # Zero-based position among element siblings.

tag               # Normalized local tag name.
namespace         # HTML, SVG, MathML, or another element namespace.
attributes        # Attribute names and string values as MAP(VARCHAR, VARCHAR).

text_direct       # Text directly inside this element before its child elements.
text_tail         # Text following this element within its parent.
```

### `material.jsonld_values`

One row represents one successfully parsed JSON-LD payload embedded in an HTML `<script>` element.

```text
content_sha256 # Identity of the containing immutable HTML bytes.
element_index  # Source <script> in material.html_elements.
type_terms     # Distinct raw @type strings found in the payload as VARCHAR[].
value          # Complete parsed JSON-LD payload as VARIANT.
```

The row identity is:

```text
UNIQUE(content_sha256, element_index)
```

`type_terms` is a mechanical summary, not a semantic classification. It lets query plans scan a
small typed column to find candidate payloads before reading `value`. The complete payload retains
single objects, arrays, and `@graph` containers without flattening their nested values.

Queries may plan in either direction:

```text
type terms -> documents -> visits -> pages
pages -> visits -> documents -> JSON-LD payloads
```

Materialization does not fetch remote contexts, expand terms, resolve relative identifiers, or
merge entities.

### `material.pages`

One row represents one unique normalized URL observed through visits.

```text
page_id            # Deterministic identity derived from normalized_url.
normalized_url     # Unique normalized URL represented by this page.

scheme             # Normalized URL scheme.
hostname           # Normalized hostname.
port               # Explicit non-default port; otherwise null.
path               # Normalized path.
query               # Preserved query string; null when absent.
registrable_domain # Public-suffix-aware domain when derivable.
```

The row identity is:

```text
UNIQUE(normalized_url)
```

URL normalization and public-suffix data are versioned materialization metadata. Visit-derived
counters, timestamps, and document pointers are not part of page identity and are omitted.

### `material.page_observations`

One row connects an observed normalized page to the immutable visit and document evidence that
supports it.

```text
page_id      # Deterministic identity of the normalized observed URL.
visit_id     # Visit that made this page observation.
document_id  # Document produced by the visit; null when none was retained.
observed_at  # Time the page representation was captured.
```

The row identity is:

```text
UNIQUE(visit_id)
```

This is the covering evidence index used for page-history, latest-document, and page-to-document
plans. Raw requested and effective URLs remain only in `ingest.visits`.

### `material.links`

One row represents a normalized source-target URL pair positively observed in HTML evidence.

```text
link_id         # Deterministic identity of the directed normalized page pair.
source_page_id  # Deterministic identity of source_url.
target_page_id  # Deterministic identity of target_url, whether visited or not.
source_url       # Normalized fragment-free URL where the link was observed.
target_url       # Normalized fragment-free URL resolved from the observed href.
relation_scope   # self, same_origin, same_host, same_site, or external.
```

The row identity is:

```text
UNIQUE(link_id)
```

`relation_scope` uses mutually exclusive precedence:

```text
self        # Source and target URL are equal.
same_origin # Scheme, hostname, and effective port are equal.
same_host   # Hostname is equal, but scheme or effective port differs.
same_site   # Registrable domain is equal, but hostname differs.
external    # Registrable domain differs.
```

Link materialization does not create rows in `material.pages`. A target may never have been
visited, but its deterministic `target_page_id` is still non-null. Page resolution happens through
an optional query-time join on page identity.

### `material.link_observations`

One row represents one anchor occurrence in one observed HTML document.

```text
link_id         # Pair identity in material.links.
document_id     # Document observation that contained the anchor.
content_sha256  # Identity of the immutable HTML bytes.
element_index   # Source anchor in material.html_elements.
raw_href        # Exact href attribute before URL resolution.
observed_at     # Time the document representation was captured.
```

The row identity is:

```text
UNIQUE(document_id, element_index)
```

Incremental refresh replaces only changed document slices. New pair identities are streamed into
`material.links`; no historical observations are scanned or merged. Earliest and latest positive
observations are ordinary query-time `MIN(observed_at)` and `MAX(observed_at)` aggregates over this
evidence table.

URL normalization, public-suffix data, and relation-scope rules are versioned materialization
metadata.

## `web.*`

`web.*` is the stable SQL interface over Atlas evidence. It is installed as versioned DuckLake
views and macros and is organized around three kinds of relation:

- identities: pages, directed links, and immutable content;
- observations: the visit, document, page, link, and crawl evidence that establishes those
  identities;
- projections: queryable structure derived from immutable content.

The base relations preserve their factual grain. They do not silently select the latest
observation, collapse history, or infer that something is current. Reductions such as "latest",
"first", "changed", or "currently present" must be requested explicitly.

The public content key is `content_id`. It is the same content-addressed value stored physically as
`content_sha256`, exposed under one semantic name throughout `web.*`. It is not a second identity
or a compatibility alias. A `document_id` identifies an observation of a representation; a
`content_id` identifies its immutable logical bytes. Many documents may therefore refer to the
same content.

### Identity relations

#### `web.pages`

One row represents one normalized page identity observed through a visit.

```text
page_id            # Deterministic identity derived from url.
url                # Unique normalized URL represented by this page.

scheme             # Normalized URL scheme.
hostname           # Normalized hostname.
port               # Explicit non-default port; otherwise null.
path               # Normalized path.
query               # Preserved query string; null when absent.
registrable_domain # Public-suffix-aware domain when derivable.
```

The row identity is:

```text
UNIQUE(page_id)
UNIQUE(url)
```

`url` is the normalized logical URL. Exact requested and effective URLs remain observation
evidence in `web.visits`. Link targets do not create page rows: a target may have a deterministic
page identity in `web.links` without having been visited.

#### `web.links`

One row represents one normalized directed page pair that has been positively observed in HTML.

```text
link_id         # Deterministic identity of the directed normalized page pair.
source_page_id  # Deterministic identity of source_url.
target_page_id  # Deterministic identity of target_url, whether visited or not.
source_url      # Normalized fragment-free URL where the link was observed.
target_url      # Normalized fragment-free URL resolved from an observed href.
relation_scope  # self, same_origin, same_host, same_site, or external.
```

The row identity is:

```text
UNIQUE(link_id)
```

This relation says that Atlas has evidence for the pair, not that the link is still present.
Occurrence history and exact anchor evidence belong to `web.link_observations`. Source and target
roles remain explicit instead of overloading a generic `page_id`.

#### `web.content`

One row represents one unique captured logical byte payload.

```text
content_id     # SHA-256 content identity of the uncompressed logical bytes.
content_bytes  # Size of the uncompressed logical bytes.
```

The row identity is:

```text
UNIQUE(content_id)
```

This is a semantic identity projection over retained document evidence, not a second physical
copy of the bytes. Immutable source bytes remain behind the object repository boundary. Their
queryable structural projections are exposed through `web.html` and `web.jsonld`.

### Observation and provenance relations

#### `web.page_observations`

One row records a page observation made by one visit.

```text
page_id      # Normalized page identity observed by the visit.
visit_id     # Visit that made the observation.
document_id  # Retained document observation; null when the visit produced none.
observed_at  # Time the representation was captured.
```

The row identity is:

```text
UNIQUE(visit_id)
```

This is the lossless page-history relation. A page row can exist even when a particular
observation retained no document.

#### `web.link_observations`

One row records one anchor occurrence in one observed HTML document.

```text
link_id       # Directed normalized page-pair identity.
document_id   # Document observation containing the anchor.
content_id    # Immutable HTML content containing the anchor.
element_index # Exact source anchor in web.html.
raw_href      # href value before URL resolution and normalization.
observed_at   # Time the representation was captured.
```

The row identity is:

```text
UNIQUE(document_id, element_index)
```

`(content_id, element_index)` connects the occurrence to its exact `web.html` element.

#### `web.documents`

One row records one retained representation observation. A visit produces zero or one document.

```text
document_id        # Unique identity of this document observation.
visit_id           # Visit that produced the document.
page_id            # Normalized effective page identity observed by that visit.
content_id         # Identity of the immutable logical bytes.
observed_at        # Time the representation was captured.
representation     # Meaning of the bytes, such as response_body or rendered_html.
declared_media_type # Media type claimed by the source; null when unavailable.
detected_media_type # Media type determined by Atlas.
charset             # Character encoding when meaningful; otherwise null.
content_bytes       # Size of the uncompressed logical bytes.
```

The row identity is:

```text
UNIQUE(document_id)
```

`page_id` is factual denormalization from the owning visit so common page-to-document history
queries do not need an extra join. Storage keys, encodings, and stored-object sizes are physical
repository details and are not part of `web.*`.

#### `web.visits`

One row records one destination admitted during a crawl, including visits that produced no
document.

```text
visit_id       # Unique identity of this visit.
crawl_id       # Crawl that produced the visit.
requested_url  # Exact URL Atlas attempted to visit.
effective_url  # Final URL after navigation or redirects; null if unresolved.
admitted_at    # Time the destination entered the crawl.
started_at     # Time acquisition began.
observed_at    # Time a returned document was captured; null if none.
finished_at    # Time the visit reached its terminal outcome.
outcome        # Final logical result.
status_code    # Final HTTP status when available.
document_id    # Document produced by the visit; null if none.
provenance     # Typed origin of this observation.
```

The row identity is:

```text
UNIQUE(visit_id)
```

#### `web.crawls`

One row records one terminal crawl execution.

```text
crawl_id           # Unique identity of this crawl execution.
kind               # atlas or import.
graph_id           # Stable crawl-plan identity; null for imports.
graph_config_hash  # Hash of the canonical frozen graph configuration.
graph_config       # Complete frozen graph configuration as VARIANT.
root_url_count     # Number of admitted roots represented by the crawl.
started_at         # Time crawl execution began.
finished_at        # Time crawl execution stopped.
stop_reason        # Reason the crawl stopped.
```

The row identity is:

```text
UNIQUE(crawl_id)
```

### Content projections

#### `web.html`

One row represents one element in one unique immutable HTML payload.

```text
content_id       # Identity of the projected immutable HTML bytes.
element_index    # Zero-based element position in depth-first document order.
parent_index     # Parent element index; null for the root.
subtree_end_index # Exclusive end of this element's subtree in document order.
depth            # Element depth from the root.
child_index      # Zero-based position among element siblings.
tag              # Normalized local tag name.
namespace        # HTML, SVG, MathML, or another element namespace.
attributes       # Attribute names and string values as MAP(VARCHAR, VARCHAR).
text_direct      # Text directly inside this element before its child elements.
text_tail        # Text following this element within its parent.
text_content     # Derived text contained by this element's complete subtree.
```

The row identity is:

```text
UNIQUE(content_id, element_index)
```

`text_content` concatenates stored text nodes inside the selected element's subtree in document
order, preserves their stored whitespace, and excludes the selected element's own `text_tail`.
It is DOM text-content reconstruction, not browser-layout `innerText`: it does not infer CSS
visibility, generated content, line wrapping, or visual whitespace. Its portable definition must
remain projection-prunable because reconstructing text for many large subtrees can be expensive.

#### `web.jsonld`

One row represents one successfully parsed JSON-LD payload embedded in one immutable HTML
payload.

```text
content_id    # Identity of the containing immutable HTML bytes.
element_index # Source <script> element in web.html.
type_terms    # Distinct raw @type strings found in the payload as VARCHAR[].
value         # Complete parsed JSON-LD payload as VARIANT.
```

The row identity is:

```text
UNIQUE(content_id, element_index)
```

### Join contract

Stable keys express the public join graph:

```text
web.crawls
  -> crawl_id -> web.visits
  -> visit_id -> web.page_observations
  -> visit_id -> web.documents

web.pages
  -> page_id -> web.page_observations
  -> page_id -> web.documents

web.links
  -> link_id -> web.link_observations
  -> source_page_id / target_page_id -> web.pages when that endpoint was visited

web.documents
  -> document_id -> web.page_observations / web.link_observations
  -> content_id -> web.content / web.html / web.jsonld

web.link_observations
  -> (content_id, element_index) -> web.html
```

Same-named keys have the same meaning and may be joined with `USING`. Directional link endpoints
remain explicit:

```sql
SELECT source.url, destination.url
FROM web.links AS link
LEFT JOIN web.pages AS source
  ON source.page_id = link.source_page_id
LEFT JOIN web.pages AS destination
  ON destination.page_id = link.target_page_id;
```

The destination join is optional because a positively observed target need not have been visited.
Its absence says only that Atlas has no visit-backed page identity for that URL; it says nothing
about whether the page exists on the live web.

### Explicit reductions

The scalar macro:

```text
web.attribute(element_attributes, attribute_name)
```

returns one attribute value from an HTML attribute map, or `NULL` when the name is absent. It is
equivalent to DuckDB's `map_extract_value` and exists to keep common element queries concise.

Two table macros provide filtered history without changing its grain:

```text
web.page_history(page_id)
  # One row per page observation.
  # Columns: page_id, visit_id, document_id, content_id, observed_at, crawl_id,
  #          requested_url, effective_url, outcome, status_code.

web.link_history(link_id)
  # One row per anchor occurrence.
  # Columns: link_id, source_page_id, target_page_id, document_id, content_id,
  #          element_index, raw_href, observed_at.
```

Neither macro selects a latest row or guarantees result order. Callers use an explicit
`ORDER BY observed_at` when order matters.

Further convenience views and table macros may shorten common history queries, but their names
must state any reduction they perform. Interfaces such as `latest_page_documents` or
`html_changes` are added only with an active caller and their exact grain is documented here when
introduced. The unqualified base relations never mean "latest".

Portable catalogue definitions are authoritative. An optional native extension may recognize and
accelerate the same valid SQL plans, but it does not define different query semantics. See
[`QUERY.md`](QUERY.md).

Interfaces for generic JSON, XML, PDF, DOCX, CSV, and other deferred formats are not yet part of
the contract.

## `data.*`

User-owned views, tables, and maintained extractions built primarily from `web.*`.

Tables are defined by the user.
