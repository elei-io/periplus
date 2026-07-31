# Schema

Atlas has one evidence path:

```text
immutable objects + ingest.*
    -> append-only material.*
    -> runtime web.* / dom.*
```

Raw bytes and both physical schemas are immutable. Public views may change as the query API
evolves.

## `ingest.*`

The authoritative relations are:

- `ingest.crawls` — terminal crawl executions and frozen graph configuration.
- `ingest.visits` — one terminal acquisition or imported observation of a URL.
- `ingest.attempts` — ordered acquisition attempts for a visit.
- `ingest.steps` — content-completion actions within an attempt.
- `ingest.documents` — the optional immutable representation retained by a visit.

They contain observed evidence only. There is no page dimension, URL decomposition, latest-state
pointer, or materialization hint in ingestion. Inserts are idempotent only when an existing
identity has identical evidence; conflicting reuse fails. No update, correction, replacement, or
deletion path exists.

Requested and effective URLs are normalized before their frozen ingestion job is produced:
surrounding whitespace and fragments are removed, scheme and hostname are lower-cased, default
ports are removed, an empty path becomes `/`, and the query string is retained byte-for-byte.
Only absolute HTTP(S) URLs without credentials are accepted.

A document records `document_id`, `visit_id`, representation and media metadata,
`content_sha256`, logical/stored sizes, storage encoding, and a repository-relative object key.
Every visible document reference must resolve to readable immutable bytes whose logical SHA-256
matches `content_sha256`.

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
discovered hidden relation set and activate it together. Live batches register new final files. There is no
`MERGE`, `UPDATE`, `DELETE`, keyed replacement, head table, or stored aggregate.

Each file under `materialization/projections/` declares one relation's ownership grain, identity,
Arrow and DuckLake schema, partitioning, sort order, projector, validation, and description. One
content-grain projection also declares the predicate that serves as the shared content-presence
marker. File discovery is the only material registry. Its digest is frozen into rebuild and
active-generation state and includes each projection file's implementation source; any add, edit,
or delete requires redeployment and a complete rebuild.

## Public catalogue

Only `web.*` and `dom.*` are public. Their views and macros use a separate lightweight registry;
they are not materialization declarations.

### `web.page_visit`

Plain visit and retained-document evidence:

```text
page_visit_id, crawl_id
url, requested_url, final_url
admitted_at, started_at, observed_at, finished_at
outcome, http_status_code
document_id, content_id, content_size_bytes
content_representation, declared_content_type, detected_content_type
character_encoding
source_kind, source_system, source_dataset, source_record_id
```

It does not implicitly join page identity, latest state, or DOM statistics.

### `web.page`

One runtime-distinct normalized effective/requested URL:

```text
url
scheme, hostname, port, path, query_string
latest_page_visit_id, last_visited_at
```

URL components are parsed lazily. Latest selection is ordered by
`(finished_at DESC NULLS LAST, visit_id DESC)`.

### Link and DOM views

- `web.link_occurrence` exposes one observed anchor with natural page-visit,
  hostname, and relationship names over `material.link_occurrences`.
- `web.link` calculates exact first/last time and visit/content/occurrence counts at runtime.
- `web.jsonld` reads `material.jsonld_values`.
- `dom.element` reads `material.html_elements` with public structural names.
- `dom.content_stats` explicitly groups elements into `element_count` and `max_depth`; it is not joined
  onto every visit.
- `dom.get_attribute`, `dom.text_content`, `dom.query_selector`, and
  `dom.query_selector_all` operate on the keyed structural DOM.

Public content keys are named `content_id`; they are the same value stored physically as
`content_sha256`.

## `data.*`

`data.*` is user-owned SQL built from the public catalogue.
