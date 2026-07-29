# Schema

Atlas separates immutable acquisition evidence, rebuildable projections, the public query
contract, and user-owned data. Raw bytes stay in object storage.

## Type policy

Use the narrowest native DuckDB type that preserves the value: typed scalars and nested types for
known shapes, `VARIANT` for heterogeneous structured values, and `JSON` only when textual JSON is
the value.

## `ingest.*`

These source-owned relations are append-oriented acquisition evidence:

- `ingest.crawls` — terminal crawl executions and their frozen graph configuration.
- `ingest.visits` — one acquisition or observation of a URL.
- `ingest.attempts` — ordered acquisition attempts for a visit.
- `ingest.steps` — content-completion actions within an attempt.
- `ingest.documents` — one optional immutable representation retained by a visit.

`visit_id` identifies an acquisition. A visit contains the requested/effective URL, lifecycle
times, outcome, status, optional `document_id`, and typed provenance. A document contains its
`visit_id`, representation and media metadata, `content_sha256`, logical/stored sizes, encoding,
and repository-relative immutable object key. Many document observations may reference the same
content bytes.

Current crawl execution remains in Postgres. Crawl history exists only here.

## `material.*`

All material relations are Atlas-owned, versioned, and rebuildable from `ingest.*` plus immutable
objects. They are implementation details, not public SQL.

### Content projections

`material.content_stats` has one narrow row per immutable HTML content identity:

```text
content_sha256
content_bytes
dom_element_count
dom_max_depth
```

It is the compiler/cost lookup for content-centric work. It deliberately does not contain a nested
DOM.

`material.html_elements` has one row per projected element:

```text
content_sha256, element_index
parent_index, subtree_end_index, depth, child_index
tag, namespace, attributes
text_direct, text_tail
```

Rows are in depth-first document order. `subtree_end_index` is exclusive. The table is bucketed by
`content_sha256` and sorted by content then element index, so a keyed document slice can prune
before DOM reconstruction.

`material.jsonld_values` has one row per successfully parsed JSON-LD script:

```text
content_sha256, element_index, type_terms, value
```

### Page projections

`material.pages` is the normalized URL dimension:

```text
page_id, normalized_url
scheme, hostname, port, path, query, registrable_domain
```

`page_id` is deterministic from the normalized URL. Counters and “latest” state are not page
identity.

`material.page_observations` is the complete visit index:

```text
page_id, visit_id, document_id, visit_at
```

There is exactly one row per retained visit, including failures without documents. `visit_at` is
the deterministic terminal ordering time:

```text
coalesce(finished_at, observed_at, started_at, admitted_at)
```

`material.page_heads` is the narrow current pointer:

```text
page_id, visit_id, visit_at
```

There is one row per page. The winner is ordered by `(visit_at DESC, visit_id DESC)`, so the latest
visit can be a failure. Keeping this separate from history makes current-page joins cheap without
duplicating mutable columns onto every observation. Corrections replace the affected observation
slice and recompute its affected heads in the same transaction.

### Link projections

`material.links` is the canonical directed page pair plus exact rollups:

```text
link_id
source_page_id, target_page_id
source_url, target_url, relation_scope
first_seen_at, last_seen_at
visit_count, distinct_content_count, occurrence_count
```

`relation_scope` is one of `self`, `same_origin`, `same_host`, `same_site`, or `external`, in that
precedence. A target page need not have been visited; its deterministic `target_page_id` still
exists.

`material.link_occurrences` is the link equivalent of visit history:

```text
occurrence_id, link_id
visit_id, document_id, content_sha256, element_index
raw_href, observed_at
```

One row is one anchor element in one document observation. Repeated anchors, repeated visits, and
shared immutable content retain distinct evidence. `occurrence_id` is stable from
`(document_id, element_index)`. The table is bucketed by `link_id`; link rollups are recomputed
exactly from affected occurrence partitions during incremental refresh. A shadow rebuild appends
its immutable evidence in bounded commits, computes all link rollups once at the generation
boundary, and only then activates `links` and `link_occurrences` together.

### Fixed-workload ownership

The document workload parses each affected content body once and commits:

```text
content_stats, html_elements, jsonld_values, links, link_occurrences
```

The visit workload commits:

```text
pages, page_observations, page_heads
```

Incremental refresh, backfill, and shadow rebuild use the same bounded stage logic. Rebuilds scan a
pinned source snapshot, catch up in bounded batches, then atomically activate their shadow tables.

## Public SQL catalogue

The stable contract consists only of `web.*` and `dom.*`. Public content keys are named
`content_id`; this is the same content-addressed value stored physically as `content_sha256`, not a
second identity.

### `web.page`

One canonical normalized URL identity:

```text
page_id, url
scheme, hostname, port, path, query, registrable_domain
```

It does not silently carry “latest success” or history columns.

### `web.visit`

Complete page acquisition history:

```text
visit_id, page_id, url, is_latest
crawl_id, requested_url, effective_url
admitted_at, started_at, observed_at, finished_at
outcome, status_code
document_id, content_id, content_bytes
representation, declared_media_type, detected_media_type, charset
dom_projection_complete, dom_element_count, dom_max_depth
provenance
```

`is_latest` is an exact join to `material.page_heads`; a failed terminal visit may be latest.
Document and DOM fields are null/false when no retained representation exists.

### `web.link`

One canonical directed relationship with exact retained-history rollups:

```text
link_id, source_page_id, target_page_id
source_url, target_url, relation_scope
first_seen_at, last_seen_at
visit_count, distinct_content_count, occurrence_count
```

### `web.link_occurrence`

Exact historical evidence for a relationship:

```text
occurrence_id, link_id, visit_id
source_page_id, target_page_id
document_id, content_id, element_index, observed_at
raw_href, resolved_url, relation_scope
```

The `(content_id, element_index)` pair joins directly to `dom.elements`; `visit_id` joins directly
to `web.visit`.

### Other public evidence

- `web.crawls` exposes terminal crawl execution evidence.
- `web.jsonld` exposes parsed JSON-LD values keyed by `(content_id, element_index)`.
- `dom.elements` exposes the flat structural DOM.
- `dom.get_attribute(attributes, name)` returns an exact attribute value.
- `dom.text_content(content_id, element_index)` returns standards-shaped descendant text for one
  keyed element.

With the matching Atlas extension installed, setup also exposes:

```sql
dom.query_selector(content_id, css_selector)
dom.query_selector_all(content_id, css_selector)
```

Both are table functions returning complete `dom.elements` rows. `query_selector` returns at most
the first match in document order. `query_selector_all` returns all matches in document order.
They materialize only the keyed document slice, not a scope-wide nested DOM. Without the extension,
the portable relational DOM and text/attribute operations remain available and selector functions
are intentionally absent from metadata.

### Join contract

```text
web.page.page_id
  -> web.visit.page_id
  -> web.link.source_page_id / target_page_id

web.visit.visit_id
  -> web.link_occurrence.visit_id

web.visit.content_id
  -> dom.elements.content_id
  -> web.jsonld.content_id

web.link.link_id
  -> web.link_occurrence.link_id
```

There are no public `page_history`, `link_history`, document-list, nested-DOM, or `web.content`
compatibility surfaces.

## `data.*`

User-owned views, tables, and maintained extractions built from the public catalogue.
