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

- `ingest.crawls` — bounded executions of crawl graphs.
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
graph_id           # Stable logical identity of the crawl graph; null for imports.
graph_config_hash  # Hash of the canonical frozen graph configuration.
graph_config       # Complete frozen graph configuration as VARIANT.
root_url_count     # Number of root URLs admitted to the initial frontier.
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
- `material.links` — observed links reduced by source and target URL.

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

`type_terms` is a mechanical summary, not a semantic classification. It lets the compiler scan a
small typed column to find candidate payloads before reading `value`. The complete payload retains
single objects, arrays, and `@graph` containers without flattening their nested values.

The compiler may plan in either direction:

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

This is the covering evidence index used for page-history, latest-document, and
page-to-document compiler plans. Raw requested and effective URLs remain only in `ingest.visits`.

### `material.links`

One row represents a normalized source-target URL pair positively observed in HTML evidence.

```text
source_page_id  # Deterministic identity of source_url.
target_page_id  # Deterministic identity of target_url, whether visited or not.
source_url       # Normalized fragment-free URL where the link was observed.
target_url       # Normalized fragment-free URL resolved from the observed href.
relation_scope   # self, same_origin, same_host, same_site, or external.

first_seen_at    # Earliest positive observation of this pair.
last_seen_at     # Latest positive observation of this pair.
```

The row identity is:

```text
UNIQUE(source_url, target_url)
```

`relation_scope` uses mutually exclusive precedence:

```text
self        # Source and target URL are equal.
same_origin # Scheme, hostname, and effective port are equal.
same_host   # Hostname is equal, but scheme or effective port differs.
same_site   # Registrable domain is equal, but hostname differs.
external    # Registrable domain differs.
```

Link materialization does not create rows in `material.pages`. A target may never have been visited,
but its deterministic `target_page_id` is still non-null. Page resolution happens through an
optional query-time join on page identity.

`first_seen_at` and `last_seen_at` are derived with `MIN` and `MAX` over immutable positive
observations, making refresh and replay idempotent. They do not claim that a link remained present
in later visits.

URL normalization, public-suffix data, and relation-scope rules are versioned materialization
metadata.

## `web.*`

The stable semantic interface implemented by the Atlas compiler:

- `web.crawls`
- `web.visits`
- `web.documents`
- `web.pages`
- `web.links`
- `web.dom`
- `web.jsonld`

Interfaces for generic JSON, XML, PDF, DOCX, CSV, and other deferred formats are not yet part of
the contract.

Columns and exact interfaces: TODO.

## `data.*`

User-owned views, tables, and maintained extractions built primarily from `web.*`.

Tables are defined by the user.
