# Catalogue SQL

Atlas exposes the managed DuckLake schema as the default SQL namespace. Query `documents`,
`crawls`, and `elements` directly; the `atlas.main` prefix is optional.

## Content and crawl identity

`documents` contains unique, content-addressed captured HTML. `elements` contains one parsed DOM
projection per document. `crawls` contains URL acquisition attempts, so joining a document to
crawls can return the same document more than once when identical content was observed by
multiple crawls.

The effective crawl URL is `page_url`, defined as `coalesce(final_url, normalized_url)` at
ingestion. Its non-null components are available as `url_scheme`, `url_host`, `url_port`,
`url_registrable_domain`, `url_path`, and `url_query`.

```sql
SELECT page_url, captured_at, duration_ms
FROM crawls
WHERE url_host = 'docs.example.com'
  AND url_path LIKE '/guides/%'
  AND captured_at >= TIMESTAMPTZ '2026-07-01 00:00:00+00'
  AND captured_at <  TIMESTAMPTZ '2026-08-01 00:00:00+00';
```

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
get_attribute(attributes, name)
has_attribute(attributes, name)
has_text(value)
text_content(document_id, element_index)
inner_html(document_id, element_index)
readable_text(document_id, element_index)
resolve_url(source, href)
```

`get_attribute` returns `NULL` for an absent attribute. `has_attribute` distinguishes an absent
attribute from a present attribute whose value is the empty string, as commonly occurs with HTML
boolean attributes.

`has_text` returns true when a value contains at least one character other than HTML whitespace
(space, tab, carriage return, line feed, or form feed). Use `has_text(text_direct)` or
`has_text(text_tail)` to ignore empty and formatting-only DOM text without changing stored values.

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
  get_attribute(attributes, 'href') AS href,
  text_content(document_id, element_index) AS exact_text,
  readable_text(document_id, element_index) AS readable
FROM elements
WHERE tag = 'a'
  AND has_attribute(attributes, 'href')
LIMIT 100;
```

The helpers scan the selected subtree. Filter and bound element sets before applying them across a
large catalogue.

`resolve_url` resolves an absolute or relative URL reference against a source page URL using
standard URL joining rules. It handles parent paths, root-relative paths, protocol-relative URLs,
queries, and fragments without changing the stored `href` attribute.

```sql
SELECT
  c.page_url AS source,
  get_attribute(e.attributes, 'href') AS href,
  resolve_url(c.page_url, get_attribute(e.attributes, 'href')) AS url
FROM crawls c
JOIN elements e USING (document_id)
WHERE e.tag = 'a'
  AND has_attribute(e.attributes, 'href');
```

## Crawl graph edges

The crawl-graph target model uses catalogue SQL to derive subsequent URL inputs from durable crawl
evidence. Edge queries are evaluated once per ready source crawl and bind `$crawl_id`; they do not
scan a node's unbounded history to decide what follows one page.

```sql
SELECT url
FROM views.page_links
WHERE crawl_id = $crawl_id
  AND is_http
  AND NOT is_internal
ORDER BY element_index
LIMIT 10;
```

SQL `LIMIT` expresses the intended number of candidates. Catalogue execution still applies hard
row, byte, memory, and timeout limits. Returned URLs become independently claimable crawl requests;
URL matching selects their crawl policy, and deployment hard ceilings protect against runaway graph
runs. See [Crawl Graphs](CRAWL_GRAPHS.md) for readiness, recursion, messaging, and state ownership.
Catalogue execution also requests Resource Governor capacity before starting; SQL bounds do not
replace global DuckLake or object-store admission.

## Physical layout

`crawls` is partitioned by year, month, and day of `captured_at`, matching common site/path/time
queries. Deduplicated `documents` and `elements` are not date-partitioned because one document can
be observed by crawls on multiple dates. `elements` is not partitioned by tag so complete
document-order projections remain physically cohesive.
