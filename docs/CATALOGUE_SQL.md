# Catalogue SQL

Atlas exposes the managed DuckLake schema as the default SQL namespace. Query `documents`,
`artifacts`, `crawls`, `crawl_attempts`, `crawl_steps`, and `elements` directly; the `atlas.main`
prefix is optional. [LAKE_SCHEMA.md](../LAKE_SCHEMA.md) is the canonical physical contract.

The SQL workbench submits queries to Atlas API and consumes Arrow IPC streams. The API owns bounded,
reusable Quack client connections while the private Quack service owns analytical CPU, memory, and
DuckLake access. The browser does not load DuckDB or receive Quack, Postgres, object-store, or
DuckLake attachment credentials.

Every request uses one explicit mode. **Run** executes the query and returns its rows, **Explain**
returns DuckDB's JSON plan without executing the query, and **Explain analyze** executes it and
returns the measured JSON plan. Run is the default; changing modes never rewrites the SQL saved by
the user. The API validates one read-only public-catalogue query and sends it to Quack without
changing the authored query identity. Before execution, the catalogue compiler may expand managed
definitions and apply proven semantics-preserving rewrites. External file and table scans, dynamic
SQL, secret inspection, system relations, and non-Atlas catalogues are rejected. Execution also has
explicit timeouts, deployment-wide concurrency, row, result-byte, and cancellation limits.

## Content and crawl identity

`documents` contains unique, content-addressed captured HTML, `artifacts` contains unique retained
non-HTML bytes, and `elements` contains one parsed DOM projection per document. `crawls` is one
self-describing logical observation. Its normalized requested and effective URLs are stored
directly; `scheme`, `host`, `port`, `registrable_domain`, `path`, and `query` describe the effective
URL. Network history is normalized into ordered `crawl_attempts`, while browser completion evidence
is in `crawl_steps`. `views.urls` is a distinct convenience projection over observed effective
URLs, not a physical dimension.

```sql
SELECT c.url, c.started_at, c.completed_at
FROM crawls AS c
WHERE c.host = 'docs.example.com'
  AND c.path LIKE '/guides/%'
  AND c.completed_at >= TIMESTAMPTZ '2026-07-01 00:00:00+00'
  AND c.completed_at <  TIMESTAMPTZ '2026-08-01 00:00:00+00';
```

## From evidence to analysis

The physical tables describe captured evidence, not universal semantic entities. Products, claims,
people, organizations, places, and concepts belong in user-authored SQL whose definitions state how
Atlas should infer them. CTEs, macros, and virtual views may organize those definitions without
changing their raw-lake inputs.

Derived observations should retain `crawl_id`, `document_id`, `url`, and a capture timestamp
wherever those values are meaningful. Additional extractor-version, confidence, or matching
evidence lets downstream users inspect why two observations were normalized together. Aggregates
such as consensus, propagation, ownership, emergence, or disappearance can then refer back to the
pages that produced them.

The baseline analytical contract is direct execution over `crawls`, `documents`, `elements`,
`crawl_attempts`, `crawl_steps`, and `artifacts`, including through user-authored virtual views. A
user may materialize a useful derived relation, but benchmark correctness and ordinary query
viability cannot depend on doing so.

[Vision](VISION.md) describes the analytical thesis and
[Analytical benchmarks](ANALYTICAL_BENCHMARKS.md) defines the ground-truth workloads used to prove
it.

## DOM columns

Elements are stored in depth-first document order. `element_index` identifies an element within
one document, `parent_index` points to its parent, and the inclusive range from `element_index` to
`subtree_end_index` contains its complete subtree. `depth` starts at zero for the parsed document
element.

`text_direct` is character data immediately inside the element before its first child.
`text_tail` is character data immediately following that element in its parent. Both are non-null
and preserve parsed whitespace.

## DOM-style helpers

The SQL helpers use snake case while following the corresponding browser DOM read semantics:

```sql
macros.get_attribute(attributes, name)
macros.has_attribute(attributes, name)
macros.has_text(value)
macros.text_content(document_id, element_index)
macros.inner_html(document_id, element_index)
macros.readable_text(document_id, element_index)
macros.resolve_url(source, href)
macros.normalize_url(value)
macros.url_parts(value)
```

These managed scalar macros are reconciled from `fixtures/scalar_macros/` during Atlas setup,
shown as read-only system definitions in the Macros UI, and deployed into the `macros` schema.
Catalogue bootstrap itself only establishes the physical schema. User-defined scalar and table
macros share this schema; DuckDB distinguishes their kinds even when their names are identical.

`get_attribute` returns `NULL` for an absent attribute. `has_attribute` distinguishes an absent
attribute from a present attribute whose value is the empty string, as commonly occurs with HTML
boolean attributes.

`url_parts` returns `NULL` for absent query and fragment components. A non-empty query or fragment
is returned without its leading `?` or `#`.

`has_text` returns true when a value contains at least one character other than HTML whitespace
(space, tab, carriage return, line feed, or form feed). Use `macros.has_text(text_direct)` or
`macros.has_text(text_tail)` to ignore empty and formatting-only DOM text without changing stored
values.

`text_content` follows DOM `Node.textContent`: it concatenates all parsed descendant character
data in DOM order, preserves whitespace, includes `script`, `style`, template, and hidden content,
and excludes the selected element's tail.

`inner_html` returns deterministic canonical serialization of the selected element's projected
children. It is not a byte slice of captured HTML: Atlas sorts attributes, applies canonical
escaping, and omits source constructs that are not part of the elements projection, such as
comments. Immutable raw HTML remains the source-preserving representation.

`readable_text` is Atlas's non-layout-aware sibling of browser `HTMLElement.innerText`. It inserts
separation at common structural element boundaries and collapses whitespace for reading, but it
does not inspect computed CSS and deliberately does not hide content. It can therefore expose
text a browser user would not see, including hidden, `script`, `style`, and template content. Use
`text_content` when exact parsed character data matters.

```sql
SELECT
  macros.get_attribute(attributes, 'href') AS href,
  macros.text_content(document_id, element_index) AS exact_text,
  macros.readable_text(document_id, element_index) AS readable
FROM elements
WHERE document_id =
      '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
  AND tag = 'a'
  AND macros.has_attribute(attributes, 'href')
ORDER BY element_index
LIMIT 100;
```

The helpers scan the selected subtree. Filter and bound element sets before applying them across a
large catalogue.

`resolve_url` resolves an absolute or relative URL reference against a source page URL using
standard URL joining rules. It handles parent paths, root-relative paths, protocol-relative URLs,
queries, and fragments without changing the stored `href` attribute.

```sql
SELECT
  c.url AS source,
  macros.get_attribute(e.attributes, 'href') AS href,
  macros.resolve_url(
    c.url,
    macros.get_attribute(e.attributes, 'href')
  ) AS url
FROM crawls c
JOIN elements e USING (document_id)
WHERE c.registrable_domain = 'example.com'
  AND c.completed_at >= TIMESTAMPTZ '2026-07-01 00:00:00+00'
  AND c.completed_at <  TIMESTAMPTZ '2026-07-02 00:00:00+00'
  AND e.tag = 'a'
  AND macros.has_attribute(e.attributes, 'href');
```

## Query selectors

Atlas provides two pure-SQL table macros over the structural `elements` projection:

```sql
macros.query_selector_all(selector [, document_id])
macros.query_selector(selector [, document_id])
```

`query_selector_all` returns every matching element. `query_selector` returns the first match in
DOM order for each selected document. Passing a content-addressed `document_id` scopes the work to
one document. Omitting it describes a catalogue-wide selector and is therefore unsuitable for the
interactive query boundary unless an outer relationship supplies a compiler-proven finite document
scope. Prefer an explicit document scope for interactive queries, and add
`ORDER BY document_id, element_index` whenever result order matters.

An unscoped selector is a deliberate analytical catalogue scan. The interactive compiler rejects
unbounded managed `elements` work before execution. Broad selectors belong in an explicitly
budgeted analysis or materialization whose initial bootstrap can be long-running and whose later
refreshes are scoped to changed documents.

```sql
SELECT
  macros.get_attribute(attributes, 'href') AS href,
  macros.readable_text(document_id, element_index) AS label
FROM macros.query_selector_all(
  'main article.card > a[href]',
  '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
)
ORDER BY element_index;
```

The supported subset covers type and universal selectors, IDs, classes, selector lists,
descendant/child/adjacent/general-sibling combinators, attribute presence and the `=`, `^=`, `$=`,
`*=`, `~=`, and `|=` operators, plus `:first-child`, `:last-child`, `:only-child`,
`:nth-child(<integer>)`, and `:empty`. CSS escaping, namespaces, attribute case flags, general
`an+b` formulas, and logical or relational pseudo-classes such as `:not()`, `:is()`, and `:has()`
are intentionally unsupported and produce an error.

The selector is parsed and evaluated inside DuckDB. There is no separate browser selector engine or
generated binding layer. The catalogue compiler may expand the managed macro to prove document
scope or apply ordinary relational rewrites, while the authored SQL remains the public query
identity executed through Quack.

## Seeded views

`views.page_metadata` returns one row per document with common metadata extracted from the projected
DOM:

```sql
SELECT
  document_id,
  language,
  title,
  description,
  canonical_href,
  robots,
  open_graph_title
FROM views.page_metadata;
```

The view also exposes `base_href`, `open_graph_description`, and `open_graph_image`. Empty metadata
values become `NULL`, HTML whitespace is collapsed in human-readable fields, and when a document
repeats a metadata field the first non-empty value in DOM order wins.

`canonical_href`, `base_href`, and `open_graph_image` retain the attribute value from the document;
apart from surrounding HTML whitespace, they are not rewritten or resolved automatically. A
content-addressed document can be observed under more than one page URL, so URL resolution requires
crawl context:

```sql
SELECT
  c.crawl_id,
  c.url AS page_url,
  macros.resolve_url(c.url, m.canonical_href) AS canonical_url,
  m.title,
  m.description
FROM crawls AS c
JOIN views.page_metadata AS m USING (document_id)
WHERE c.outcome = 'success';
```

`views.passages` returns searchable, human-scale regions from headings, paragraphs, list items,
quotes, preformatted blocks, table cells, and figure captions. Each row retains its `document_id`,
`element_index`, and source tag alongside normalized readable text. It is an ordinary seeded
materialization driven by document-table DML ticks: search reads its durable
backing relation while the standard materialization worker replaces only the
affected `document_id` groups.

`views.json_ld_scripts` preserves one row per
`<script type="application/ld+json">`. `json_text` contains the exact script text, while
`is_valid`, `json_value`, and `root_type` expose its parse state without dropping malformed or empty
scripts.

`views.json_ld_nodes` provides the object-level analytical surface for valid scripts. A root object
without an `@graph` array produces one node, a root array produces one node per object item, and an
`@graph` array produces one node per object member. Scalar array items are not nodes. Each row
retains its source `document_id`, `element_index`, script and node ordinals, JSON path, effective
top-level context, optional `@id`, normalized `@type` list, and complete `node_json`.

```sql
SELECT
  document_id,
  node_id,
  json_extract_string(node_json, '$.name') AS name,
  json_extract_string(node_json, '$.offers.price') AS price,
  json_extract_string(node_json, '$.offers.priceCurrency') AS currency
FROM views.json_ld_nodes
WHERE list_contains(node_types, 'Product');
```

Nested objects remain inside their containing node. Atlas does not recursively flatten every JSON
path or claim to perform full JSON-LD context expansion. Use DuckDB's `json_tree` against
`json_ld_scripts.json_value` when a path/value representation is useful for a specific query.

Atlas seeds two table macros for typed JSON-LD extraction. Start with
`macros.suggest_json_ld_schemas(url)`. It returns one row per discovered `@type`, combining the
structure of every matching node of that type. Each row includes `entity_type`, matched crawl,
document, and node counts, history bounds, the inferred DuckDB JSON schema, an example node, and a
ready-to-run `extract_sql` query.

```sql
SELECT *
FROM macros.suggest_json_ld_schemas(
  'https://scrapeme.live/shop/%'
);
```

The URL follows the same rules as the record macros: it is an exact match unless it contains `%`,
in which case it is an `ILIKE` pattern. Untyped nodes are omitted. A node with multiple normalized
values in `views.json_ld_nodes.node_types` contributes to each corresponding schema row.

The returned query passes `inferred_schema` to
`macros.extract_json_ld(url, type, schema)`. That macro parses each complete node into a typed
row whose columns are the JSON-LD fields, alongside its crawl and node provenance. `SELECT *`
therefore produces clean structured output without writing JSON paths or expanding an intermediate
struct:

```sql
SELECT *
FROM macros.extract_json_ld(
  'https://scrapeme.live/shop/%',
  'Product',
  '{"name":"VARCHAR","sku":"VARCHAR","description":"VARCHAR"}'
);
```

Use the complete suggested schema when data varies across pages; DuckDB fills absent fields with
`NULL` and retains nested objects and arrays as nested typed values. The result begins with crawl
and node provenance, continues with the inferred entity fields, and ends with raw `node_json`.

## Automatic record discovery

Atlas seeds two deterministic table macros for turning captured directory pages into records.
The URL argument uses exact matching unless it contains `%`; a value containing `%` is an `ILIKE`
pattern matched against both the requested normalized URL and the effective final page URL. The
macros include every successful matching crawl observation across graph runs.

The result limit does not bound input history. Interactive execution still requires the compiler
to prove that the matched crawls produce a document population below the configured document and
element ceilings. Broader historical discovery belongs in a materialization or an explicitly
budgeted analytical run.

`macros.suggest_records(url)` ranks repeated sibling structures and returns up to ten candidates.
Each candidate includes its record selector, total matched record count, matched crawl and page
counts, history bounds, score, example HTML, twelve ranked field definitions, and a ready-to-run
`extract_sql` query. The ranking rewards structural consistency, information density, stable field
coverage, safe relative selectors, and useful value shapes such as prices and URLs. It penalizes
fragmented structures, presentation classes, duplicate-equivalent fields, and positional image
labels.

```sql
SELECT *
FROM macros.suggest_records(
  'https://books.toscrape.com/catalogue/category/books/%'
);
```

Pass one returned `record_selector` to `macros.extract_records(url, record_selector)`. It returns
one row per record observation with its `crawl_id`, `graph_run_id`, `captured_at`, and `page_url`,
plus stable `field_1` through `field_12` columns. `field_definitions` maps those positions to their
relative selector and value source; `record_json`, `record_text`, and `record_html` retain lossless
and diagnostic forms. A field with one value is returned as plain text, while a genuinely
multi-valued field is returned as a JSON array string.

```sql
SELECT *
FROM macros.extract_records(
  'https://books.toscrape.com/catalogue/category/books/%',
  'ol.row > li.col-lg-3.col-md-3.col-sm-4.col-xs-6'
)
WHERE captured_at >= TIMESTAMPTZ '2026-07-01 00:00:00+00'
  AND captured_at <  TIMESTAMPTZ '2026-08-01 00:00:00+00';
```

These macros infer structure and value sources; they do not assign semantic names such as `price`
or `title`. Numbered fields keep extraction free of site-specific rules and runtime model calls.
The selectors are schematic, include values from the record root, group adjacent companion
siblings, and restrict class-derived values to mutually exclusive semantic variants. Extraction
returns at most 2,000 records per crawl observation. Its `matched_record_count` and
`records_truncated` columns make that per-observation safety bound explicit.

## Crawl graph edges

The crawl-graph target model uses bounded SQL over a verified navigation package derived from
durable raw HTML. Edge queries are evaluated once per ready source crawl and bind `$crawl_id`; they
do not scan a node's unbounded history to decide what follows one page.

```sql
SELECT target_url AS url
FROM edge.page_links
WHERE crawl_id = $crawl_id
  AND relation_kind <> 'external'
ORDER BY element_index
LIMIT 10;
```

`edge.page_links` is an ephemeral relation derived from the current source page. It exists only while
evaluating that page's outgoing graph edges and is independent of every user-owned `views.*`
definition. It contains one row per valid HTTP(S) anchor occurrence:

| Column | Meaning |
|---|---|
| `crawl_id`, `document_id`, `captured_at` | Source crawl, retained document, and acquisition time |
| `source_url`, `target_url` | Fragment-free normalized source and crawl destination |
| `source_*`, `target_*` | Scheme, host, effective port, path, and query components; the source also carries its authoritative registrable domain |
| `target_fragment` | Fragment retained independently from crawl identity |
| `relation_kind` | Most-specific URL relationship: `same_url`, `same_path`, `same_origin`, `same_host`, `same_site`, or `external` |
| `raw_href` | Original anchor `href`, before resolution and URL normalization |
| `element_index` | Exact source anchor in the durable DOM projection and its document order |

Distinct anchors are not collapsed merely because they produce the same crawl URL. This preserves
separate fragments and exact DOM provenance; graph admission still normalizes and deduplicates
returned crawl candidates. Fragment navigation can therefore be inspected without creating
fragment-specific crawl identities. Link labels and presentation attributes are deliberately absent:
the relation describes URL topology, not anchor content.

The seeded historical `views.page_links` relation stores `source_url` and `target_url` directly.
Its self-contained definition derives links from `crawls` and `elements`, and its placement under
`fixtures/materialized_views/crawl/` activates a materialization driven by crawl-table ticks.
The materialization worker creates and fully populates an incarnation-named backing relation under
`_atlas_materializations`, then replaces the public view with a thin wrapper over that private
table. Public view names and physical table names are deliberately independent, so arbitrary
user-created views cannot collide with their backing tables across schemas. Interactive reads fail
fast while an intended materialization is creating, failed, blocked, or deleting instead of
executing its potentially expensive source definition.
The worker then replaces affected `crawl_id` groups after coalesced ticks. Historical graph edges never wait
for either pipeline and may observe an incomplete recent materialization refresh at their pinned
pre-run snapshot; this intentional consistency contract is defined in
[Crawl graphs](CRAWL_GRAPHS.md#historical-consistency-contract).

```sql
SELECT
  macros.url_parts(source_url).host AS source_host,
  macros.url_parts(target_url).host AS target_host,
  count(*) AS links
FROM views.page_links
WHERE relation_kind = 'external'
GROUP BY source_host, target_host
ORDER BY links DESC;
```

Page-only SQL runs in a standalone memory-limited DuckDB connection. An edge may join catalogue
relations; Atlas pins those reads to the graph run's pre-run DuckLake snapshot so redelivery cannot
observe catalogue ingestion arriving midway through the run. Edge SQL may not open external files
or table functions.

SQL `LIMIT` expresses the intended number of candidates. Catalogue execution still applies hard
row, byte, memory, and timeout limits. Returned URLs become independently claimable crawl requests;
URL matching selects their crawl policy, and deployment hard ceilings protect against runaway graph
runs. See [Crawl Graphs](CRAWL_GRAPHS.md) for readiness, recursion, messaging, and state ownership.
Page-only execution requests object-read capacity. Historical joins request catalogue and
object-read capacity before starting; SQL bounds do not replace shared infrastructure admission.

## Physical layout

`crawls` is partitioned by `day(completed_at)` and sorted by effective site, path, completion time,
and crawl identity. `documents` and `elements` share `bucket(64, document_id)` partitioning;
`elements` is sorted by `(document_id, element_index)` so a document projection remains cohesive
without producing one partition per document. Attempts and completion steps use daily start-time
partitions and crawl-local sorting. The seeded `views.page_links` backing table also carries
`captured_at` and uses daily partitioning. Seeded
materialized views use the same `_atlas_materializations` backing-table machinery,
filtered NATS consumer, and worker ownership as user-created materializations; there is no private page-links table or
ingestion exception.

Run `make catalogue-benchmark` against an existing catalogue before changing that layout. The
benchmark covers day-bounded crawl reads, document-scoped CSS selection, a day-bounded
crawl-to-elements join, and bounded readable-text extraction. It reports the first execution,
repeated warm timings, result size, and a warm `EXPLAIN ANALYZE` profile with scan and intermediate
cardinality metrics. The first execution is not described as a cold-cache measurement because the
embedded process cannot reliably evict operating-system and object-store caches.
