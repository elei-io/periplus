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
node tree, including stored integer depth (document root = 0). It shares the
parse context and position space with elements and links.
The `node_index = 0` document row is the generation content-presence marker.

### `material.html_elements`

One row per `(content_sha256, element_index)`:

```text
content_sha256, element_index
parent_index, subtree_end_index, depth, child_index
tag, namespace, attributes
text_direct
```

The private `element_index` column uses the complete node position space: element
positions may have gaps. `child_index` counts all sibling nodes. `text_direct`
concatenates immediate text children.
Rows are depth-first. `subtree_end_index` is exclusive. The document root in `material.html_nodes` is the content-projection
presence marker; no content manifest or statistics row is maintained. That marker permanently
prevents later visits from re-emitting content-grain HTML rows, while a deterministic
minimum document identity selects one owner when genuinely new content first appears in parallel
batches.

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

Material batches write immutable Parquet and atomically replace their deterministic owned
identities before registering files. Rebuilds create the complete hidden relation set and
activate it together. Postgres receipts make completed redelivery a no-op; replacement
makes a lost receipt safe to replay. There is no head table or stored aggregate.

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

One acquisition with retained HTML (`lower(detected_media_type) = 'text/html'`).
Repeated captures can share content. HTTP error responses qualify when retained as
HTML; non-HTML documents and attempts without content do not appear. `capture_id` reuses the internal visit identity without a second lifecycle.

| Column | SQL type | Meaning |
| --- | --- | --- |
| capture_id | UUID | Acquisition identity |
| requested_url | VARCHAR | Normalized requested URL |
| effective_url | VARCHAR | Final URL when known |
| captured_at | TIMESTAMPTZ | Capture time |
| http_status_code | INTEGER | Response status when known |
| content_id | VARCHAR | SHA-256 identity of retained logical bytes, non-null |
| byte_length | BIGINT | Length of retained logical bytes before storage compression, non-null |
| encoding | VARCHAR | Detected character encoding when meaningful, otherwise null |
| request_ids | UUID[] | Sorted unique coverage request IDs, non-null; empty when no membership evidence is visible |

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

### `public_v1.html_node` and `public_v1.html_element`

Both relations share `content_id VARCHAR`, `node_index INTEGER`,
`parent_index INTEGER`, `subtree_end_index INTEGER`, `sibling_index INTEGER`,
`depth INTEGER`.
Identity is `(content_id, node_index)` within the returned catalogue snapshot.
Positions are zero-based depth-first positions across **all** nodes, including the
document root. Subtree end is exclusive. Parent is null only for the document
root. Sibling positions count all node kinds. Depth counts parent edges from the
document root: document = 0, html = 1, and each child is one deeper than its parent.
The same element has the same depth in both relations; depth is structural, not
heading rank or visual importance.

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

HTML projections are asynchronous. A capture with no matching node root may be
awaiting materialization; absence does not prove an empty document.
HTML readiness uses the content document-root presence marker. Use LEFT JOIN when
retaining captures without available structure matters.

### `public_v1.html_form`, `html_form_control`, and `html_select_option`

These content-owned views describe captured HTML form structure. They add no
stored projections, downloads, browser execution, or submissions. All three keys
are `(content_id, node_index)`. Only HTML-namespace elements are included.

`html_form` has these columns, in order: `content_id VARCHAR`, `node_index INTEGER`,
`id VARCHAR`, `name VARCHAR`, `action VARCHAR`, `method VARCHAR`, `enctype VARCHAR`,
`target VARCHAR`. Each row represents a form. Values are declared parsed attributes;
missing attributes are null, empty ones remain empty, case is preserved, and
relative actions stay relative. Missing method is not replaced by get.

`html_form_control` has these columns, in order:

| Column | SQL type | Meaning |
| --- | --- | --- |
| content_id | VARCHAR | Source content identity |
| node_index | INTEGER | Source control node |
| form_node_index | INTEGER | Reconstructed owner; null when unowned |
| tag | VARCHAR | input, button, select, textarea, fieldset, output or object |
| type | VARCHAR | Declared type; no default or tag-derived value |
| name | VARCHAR | Declared name |
| value | VARCHAR | Textarea child text, otherwise declared value attribute |
| required | BOOLEAN | Attribute present on this element |
| disabled | BOOLEAN | Attribute present on this element |
| readonly | BOOLEAN | Attribute present on this element |
| multiple | BOOLEAN | Attribute present on this element |

Ownership is reconstructed from the parsed source tree: an explicit nonempty form
attribute targets the first element with that exact ID in the content. It must be
an HTML form; a preceding non-form with that ID blocks association. An empty or
unresolved explicit reference leaves the control unowned, with no ancestor fallback.
Without the form attribute, the nearest ancestor HTML form owns the control.
Controls without owners remain rows. Serialized HTML cannot preserve JavaScript
state, shadow-tree ownership, custom form-associated elements, or parser-history
associations for malformed markup; these views do not claim to reconstruct those.

Textarea value is its parsed child text, including an empty string for an empty
textarea. Other value fields are only declared attributes: no checkbox default
"on", select chosen value, output computation, selected file, or live input value
is synthesized. Boolean attributes report presence, even when spelled "false".
They do not infer inherited fieldset disabling, applicability, validation rules,
or effective browser state. Labels and additional attributes remain in primitives.

`html_select_option` has `content_id VARCHAR`, `node_index INTEGER`,
`select_node_index INTEGER`, `option_index INTEGER`, `value VARCHAR`, `text VARCHAR`,
`selected BOOLEAN`, `disabled BOOLEAN`, in that order. It includes options with
an ancestor HTML select; the nearest select owns each option. Positions start at
zero in source order across optgroups. Datalist and orphan options are excluded.
Value is the declared attribute: absence is null, without a text fallback. Text
concatenates descendant text nodes in order, excluding comments but with no
whitespace normalization or inserted separators. Selected and disabled report
attributes on the option itself; optgroup disabling and live selectedness are
not inferred. Multiple selected declarations remain visible.

```sql
SELECT f.node_index AS form_node_index, f.action, c.tag, c.type, c.name, c.required
FROM public_v1.html_form f
JOIN public_v1.html_form_control c
  ON c.content_id = f.content_id AND c.form_node_index = f.node_index
WHERE f.content_id = ?
ORDER BY f.node_index, c.node_index;
```

### `public_v1.html_list` and `public_v1.html_list_item`

Content-owned views over existing HTML primitives, with no additional stored
projection. Both keys are `(content_id, node_index)`.

`html_list` represents each HTML-namespace ul or ol, including empty lists:

| Column | SQL type | Meaning |
| --- | --- | --- |
| content_id | VARCHAR | Source content identity |
| node_index | INTEGER | Source list node |
| ordered | BOOLEAN | True for ol |
| start_number | BIGINT | Effective ordered-list start; null for ul |
| reversed | BOOLEAN | Whether an ol has the reversed attribute |

`html_list_item` represents each direct HTML li child of one of those lists:

| Column | SQL type | Meaning |
| --- | --- | --- |
| content_id | VARCHAR | Source content identity |
| node_index | INTEGER | Source item node |
| list_node_index | INTEGER | Owning list node |
| item_index | INTEGER | Zero-based position among direct li children |
| ordinal | BIGINT | Effective ordered-list number; null for ul |
| text | VARCHAR | Descendant text excluding nested ul/ol subtrees |

An ordered list starts at its valid start declaration, otherwise one, or its
number of direct HTML li children when reversed. An empty reversed list therefore
has start_number zero. Reversed is a boolean attribute: even reversed="false"
means reversed. A valid li value resets that item's number and subsequent items
continue upward or downward from it. Unordered lists ignore numbering attributes.

Signed integer prefixes after leading HTML ASCII whitespace are parsed into BIGINT;
invalid or out-of-range declarations are ignored. Numbering arithmetic uses a
wider intermediate and raises a conversion error if a resulting ordinal exceeds
BIGINT, rather than wrapping. Original declarations remain in html_element.attributes.
CSS counters, marker styles and visibility are not interpreted.

Nested lists own their items independently; only direct li children count toward
a list's numbering. Orphan li and description-list dt/dd elements are excluded.
Text concatenates descendant text nodes in order, without trimming, whitespace
normalization or inserted separators. Comments and nested ul/ol subtrees are
excluded; script/style text is included. Empty items yield empty strings.
Description lists do not receive special extraction behavior here.

```sql
SELECT item_index, ordinal, text
FROM public_v1.html_list_item
WHERE content_id = ? AND list_node_index = ?
ORDER BY item_index;
```

### `public_v1.html_jsonld`

A content-owned view over existing HTML primitives, with one row per HTML script
whose declared type has the application/ld+json media-type essence. Type matching
ignores ASCII case, surrounding HTML ASCII whitespace and semicolon parameters.
The key is `(content_id, node_index)`. There is no new materialization.

| Column | SQL type | Meaning |
| --- | --- | --- |
| content_id | VARCHAR | Source content identity |
| node_index | INTEGER | Source script node |
| value | JSON | Complete parsed document; SQL null on failure |
| parse_error | VARCHAR | Null on success; otherwise a stable parse error |

Ordered script text is parsed using DuckDB's JSON parser and its accepted syntax.
Empty/whitespace-only scripts report `Empty JSON-LD script`; other parser failures
report `Invalid JSON syntax`. Invalid declarations remain rows rather than failing
the query. A valid JSON null is JSON `null`, with no parse error, distinct from
SQL null on failure. Arrays, objects, @graph and scalar JSON values remain complete;
this is syntax parsing, not JSON-LD semantic validation. Duplicate scripts remain
separate source rows. No entity flattening, context fetching, URL resolution, RDF
expansion, or schema.org interpretation occurs. Script src URLs are not fetched;
a source-only script with no inline text is reported as empty.

Original script text is available from html_node children through the source
node. HTML script raw-text parsing does not decode entity-like strings such as
`&amp;`. Filters on content_id/node_index restrict source selection.

```sql
SELECT node_index, value ->> '@type' AS declared_type, parse_error
FROM public_v1.html_jsonld
WHERE content_id = ?
ORDER BY node_index;
```

### `public_v1.html_image`

A content-owned view with one row per HTML-namespace img element, including
images without src. It reads existing elements directly and needs no additional
materialization. The key is `(content_id, node_index)`.

| Column | SQL type | Meaning |
| --- | --- | --- |
| content_id | VARCHAR | Source content identity |
| node_index | INTEGER | Source img node |
| src | VARCHAR | Declared source reference |
| srcset | VARCHAR | Declared responsive candidates, unparsed |
| sizes | VARCHAR | Declared responsive sizing expression |
| alt | VARCHAR | Declared alternative text |
| width | VARCHAR | Declared width string |
| height | VARCHAR | Declared height string |

Attribute values retain their parsed spelling and whitespace. Missing attributes
are null, while explicitly empty values remain empty strings, including alt="".
Character references are already decoded by HTML parsing. Relative references,
data URLs, and srcset strings are unchanged; there is no URL resolution or browser
candidate selection. Width and height are declarations, not decoded-image or
rendered dimensions. Repeated image elements remain separate source rows.

This relation does not include CSS backgrounds, SVG image elements, input images,
or picture/source alternatives. An img inside picture is included normally.
There is no inference from data-src or other lazy-loading attributes; inspect
html_element.attributes through the source key for those values. Images are not
downloaded, classified, or tested for visibility by this view.

```sql
SELECT node_index, src, alt, srcset
FROM public_v1.html_image
WHERE content_id = ?
ORDER BY node_index;
```

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
text without trimming or inserted separators. Relative URLs remain relative.
The language and charset are declarations, not detection results; http-equiv is
not evidence of a received HTTP header. No metadata inference, URL resolution,
JSON-LD parsing, or body-level microdata extraction is performed.

```sql
SELECT node_index, kind, name, value
FROM public_v1.html_metadata
WHERE content_id = ?
ORDER BY node_index, kind, name;
```

### `public_v1.html_code`

A content-owned view over existing HTML primitives. One row represents each
HTML-namespace code element; the key is `(content_id, node_index)`.

| Column | SQL type | Meaning |
| --- | --- | --- |
| content_id | VARCHAR | Source content identity |
| node_index | INTEGER | Source code element node |
| block | BOOLEAN | Whether an HTML pre element is an ancestor |
| text | VARCHAR | Complete ordered descendant text |

Text preserves parsed whitespace and line breaks, including text inside syntax
highlighting spans. Comments are excluded; no separators, trimming, or language
inference are applied. Empty code yields an empty string. Character references
have already been decoded by HTML parsing; original serialization remains in raw
bytes. Nested code elements remain separate rows, with ancestor text including
its descendants. The block flag describes ancestry, not CSS layout. Plain pre,
styled containers, and foreign-namespace elements are not inferred to be code.
Language classes and other declarations remain available through html_element.
No additional materialization is required.

```sql
SELECT node_index, block, text
FROM public_v1.html_code
WHERE content_id = ?
ORDER BY node_index;
```

### `public_v1.html_section`

A content-owned view of heading-delimited passages, computed from existing HTML
primitives. One row per HTML h1–h6; the key is `(content_id, heading_node_index)`.
This is an explicit interpretation of heading order, not the HTML section element,
a semantic outline, or a CSS layout region. It adds no stored projection.

| Column | SQL type | Meaning |
| --- | --- | --- |
| content_id | VARCHAR | Source content identity |
| heading_node_index | INTEGER | Heading that starts the passage |
| parent_heading_node_index | INTEGER | Nearest preceding heading of higher rank; null when absent |
| start_node_index | INTEGER | Inclusive start immediately after the heading subtree |
| end_node_index | INTEGER | Exclusive end at the next heading of equal/higher rank, or document end |

Higher rank means a smaller heading number. An h2 passage includes following h3–h6
subsections until the next h1 or h2. Its own heading text is excluded, but descendant
subsection headings are inside its range. Parent references identify the nearest
preceding higher-ranked heading even when levels are skipped. These are passage
parents, not DOM parents. Adjacent equal-rank headings can produce empty passages.
If malformed nested headings place the next boundary before the heading subtree
ends, the passage is represented as an empty interval at its start.

Content before the first heading does not receive an artificial section. A document
with no headings has no rows. Ranges end at the document-root boundary, so the final
passage can include footer or navigation content. Layout, visibility, ARIA heading
roles and semantic relevance are not inferred. Use this rule for predominantly
linear documents; inspect results before treating a passage as a domain field.
Node references retain the same catalogue-snapshot scope as other HTML relations.

Join html_heading for the heading text. Join existing nodes, lists, tables or images
by content_id and positions in `[start_node_index, end_node_index)`. Parent and
child ranges overlap deliberately; querying both can repeat nested content.

```sql
SELECT s.heading_node_index, h.text AS heading,
       coalesce(string_agg(n.value, '' ORDER BY n.node_index), '') AS passage
FROM public_v1.html_section s
JOIN public_v1.html_heading h
  ON h.content_id = s.content_id AND h.node_index = s.heading_node_index
LEFT JOIN public_v1.html_node n
  ON n.content_id = s.content_id AND n.node_index >= s.start_node_index
 AND n.node_index < s.end_node_index AND n.node_type = 'text'
WHERE s.content_id = ? AND lower(trim(h.text)) = 'product description'
GROUP BY s.heading_node_index, h.text;
```

The example concatenates source text without separators or trimming; comments are
excluded by node_type and script/style text is included. html_section itself does
not choose a text extraction policy. Filter content before expanding passages.

### `public_v1.html_heading`

A content-owned view over the existing HTML primitives, computed on demand.
One row represents one HTML-namespace h1 through h6 element. The key is
`(content_id, node_index)`; join html_element on that key for original attributes.

| Column | SQL type | Meaning |
| --- | --- | --- |
| content_id | VARCHAR | Source content identity |
| node_index | INTEGER | Source heading node |
| level | INTEGER | Declared heading level, 1 through 6 |
| text | VARCHAR | Ordered descendant text; empty string for an empty heading |

Text concatenates all descendant text nodes in document order, including inline
markup and script/style text, without trimming, whitespace normalization, or
inserted separators. Comments are excluded. Image alt text is not substituted.
CSS visibility and ARIA heading roles are not interpreted. Levels come directly
from tag names; no section hierarchy or inferred rank is added. Repeated headings
remain separate rows, and captures sharing content share the same heading rows.
Filter by content_id and order by node_index for source order. No additional
materialization or storage is required.

```sql
SELECT node_index, level, text
FROM public_v1.html_heading
WHERE content_id = ?
ORDER BY node_index;
```

### `public_v1.html_table` and `public_v1.html_table_cell`

Both are content-owned SQL views over the HTML primitives, computed on demand.
They add no physical tables, persistence, or materialization jobs. Filter by
`content_id` and, for cell extraction, `table_node_index` before exploring cells.

`html_table` has `content_id VARCHAR`, `node_index INTEGER`,
`caption_node_index INTEGER`, and `caption VARCHAR`. One row represents one HTML
`table` element, including empty and nested tables. Its key is
`(content_id, node_index)`. The first direct HTML caption is used; both caption
columns are null when absent, while an existing empty caption has empty text.

`html_table_cell` has these columns, in order:

| Column | SQL type | Meaning |
| --- | --- | --- |
| content_id | VARCHAR | Source content identity |
| table_node_index | INTEGER | Owning table node |
| row_node_index | INTEGER | Owning tr node |
| node_index | INTEGER | Source td or th node |
| row_index | INTEGER | Zero-based parsed source row order, including empty rows |
| column_index | INTEGER | Zero-based starting grid column, accounting for spans |
| row_span | INTEGER | Effective occupied source rows within the row group |
| column_span | INTEGER | Effective occupied columns |
| is_header | BOOLEAN | True for th, without inferring header associations |
| text | VARCHAR | Ordered descendant text; empty string for an empty cell |

Its key is `(content_id, node_index)`. Join the table through
`(content_id, table_node_index)` and original attributes through
`html_element(content_id, node_index)`. Each source cell appears once, including
merged cells. Missing grid positions do not generate cells. Only HTML-namespace
cells directly under table rows are included; rows must be direct table children
or children of direct thead/tbody/tfoot elements. Nested tables own their rows and
cells independently.

Rows follow parsed source order, including tfoot wherever it occurs; these views
are not a browser/CSS layout simulation. Span attributes use the HTML nonnegative
integer prefix rule. Missing/invalid spans default to one; colspan zero becomes
one. Column spans cap at 1000 and positive row spans at 65534. Rowspan zero covers
the remaining source rows in its group. All row spans are clipped to that group's
remaining source rows; no synthetic rows are created. Original declarations are
preserved in html_element.attributes. Cells start at the next unoccupied column;
a colspan that overlaps an earlier row-spanning cell raises an explicit error.
The HTML attribute rules are described in the
[HTML standard](https://html.spec.whatwg.org/multipage/tables.html).

Text concatenates descendant text nodes in order without trimming, whitespace
normalization, or inserted separators. Comments and nested-table subtrees are
excluded; script/style text remains included, and CSS visibility is not modeled.
This rule applies to captions and cells alike. Formatting and footnote links can
be inspected through original nodes.

Grid computation rejects tables exceeding 10,000 source cells or 2,048 occupied
columns instead of emitting partial positions. Query service time/memory/result
limits still apply; LIMIT alone does not bound extraction work. These limits
apply to cell layout, not discovery through html_table.

```sql
SELECT row_index, column_index, text, row_span, column_span
FROM public_v1.html_table_cell
WHERE content_id = ? AND table_node_index = ?
ORDER BY row_index, column_index;
```

### `public_v1.link`

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
