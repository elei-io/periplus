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

## `ingest.*`

The authoritative relations are:

- `ingest.crawls` — terminal crawl executions and frozen graph configuration.
- `ingest.visits` — one terminal acquisition or imported observation of a URL.
- `ingest.attempts` — ordered acquisition attempts for a visit.
- `ingest.steps` — content-completion actions within an attempt.
- `ingest.documents` — the optional immutable content retained by a visit.

They contain observed evidence only. There is no page dimension, URL decomposition, latest-state
pointer, or materialization hint in ingestion. Inserts are idempotent only when an existing
identity has identical evidence; conflicting reuse fails. No update, correction, replacement, or
deletion path exists.

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

The public catalogue contains exactly four relations:

```text
web.observation
web.link_occurrence
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
observation_id, crawl_id
requested_url, effective_url
observed_at, outcome, http_status_code
content_id
source_kind, source_system, source_dataset, source_record_id
```

`content_id` is nullable and identifies the one retained content object when present. The relation
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

The same `content_id` may belong to observations of multiple URLs, crawls, sources, or times.
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
