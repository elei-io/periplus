# Query

Atlas delivers its query interface in three layers, in this order.

## Performance triage

Every query-performance investigation begins by classifying the issue as a schema/catalogue
design problem, a compiler/optimizer problem, or both. The classification is made before choosing
an implementation:

| Classification | Evidence | Primary response |
| --- | --- | --- |
| Schema/catalogue | The public semantics inherently require unbounded expansion, repeated expensive computation, excessive row width, or an unsuitable grain or key. | Change the contract directly or add the fixed materialization justified by an active query. |
| Compiler/optimizer | The SQL expresses bounded work, but the physical plan misses projection or predicate pushdown, chooses a pathological join or aggregation, or performs avoidable work before a limit. | Add an appropriate compiler diagnostic or a semantics-preserving plan rewrite. |
| Both | The public shape makes expensive work easy or implicit, and the compiler also produces a worse plan than those semantics require. | Correct the schema or catalogue contract first, then address the remaining compiler gap. |

The investigation records the representative query and data scale, `EXPLAIN` or
`EXPLAIN ANALYZE` evidence, required logical cardinality, observed or estimated physical
cardinality, and the classification decision. Memory limits, thread counts, and spill settings are
capacity controls; they do not substitute for this analysis.

A compiler response is not automatically a SQL rewrite:

- A warning explains a valid query whose cost is likely surprising or unintentionally unbounded.
- An error rejects a query that violates an enforced boundedness or safety contract, or for which
  Atlas cannot provide a safe execution path.
- A SQL or plan rewrite changes the physical form only when it preserves the portable public
  semantics and has differential correctness and activation tests.

Compiler behavior must not conceal a poor public schema, and schema changes must not encode around
one accidental optimizer plan. “Compiler” here means Atlas query analysis and the optional native
optimizer boundary; it does not restore the removed schema-dependent Python compiler.

## 1. Portable DuckLake catalogue

Atlas initialization installs and versions the base `web.*` and `dom.*` interface as persistent
DuckLake views, scalar macros, and table macros over `ingest.*` and `material.*`. These catalogue
objects define the portable public semantics and remain correct without an Atlas SDK or native
extension. `atlas-setup` loads the exact-version Atlas extension and installs the complete
catalogue, including extension-backed selector macros; setup fails rather than publishing a
partial Atlas installation. The objects are changed only by Atlas catalogue upgrades.

The authoritative one-object SQL definitions live under
`backend/src/atlas/platform/catalogue/sql/web/` and
`backend/src/atlas/platform/catalogue/sql/dom/`, split into `macros_scalar`, `views`, and
`macros_table`. The explicit manifest in `atlas.platform.catalogue.public` installs them in
dependency order. `atlas-setup` replaces the complete interface transactionally after reconciling
the typed physical schema, then validates object names, columns, macro kinds, view comments,
manifest descriptions, and catalogue version. Ordinary processes validate this contract and
never repair it at startup.

`dom.content_stats` calculates DOM element count and maximum depth explicitly from the structural element
projection. Those statistics are not attached to every visit. Extension-backed
`dom.query_selector(content_id, selector)` and `dom.query_selector_all(content_id, selector)` feed
only the keyed, partition-prunable `dom.element` slice into a streaming table-in/table-out native
operator. The operator reconstructs at most one document at a time and returns complete element
rows. Page-first plans should reduce and deduplicate content identities before invoking it.

Every public view and view column has a concise description derived from the semantic contract in
`SCHEMA.md`. Setup reapplies supported view comments after replacing each view. DuckLake does not
currently accept `COMMENT ON COLUMN` for view columns, so the authoritative manifest supplies
column descriptions to the SQL metadata endpoint and both shells.

Recurring expensive computations belong in Atlas-owned `material.*` relations maintained through
the ordinary materialization lifecycle.

The shared terminal and web shell accepts bounded read-only SQL over qualified `web.*` and
`dom.*` relations only. It also accepts `DESCRIBE`, `EXPLAIN`, `EXPLAIN ANALYZE`, and `SUMMARIZE`
when their target passes the same public-namespace validation, plus `SHOW TABLES FROM web` and
`SHOW TABLES FROM dom`. Its metadata and autocomplete endpoints expose public views plus scalar
and table macro signatures, including `dom.get_attribute` and `dom.text_content`, and—when
installed—the DOM selector functions, but no `ingest.*` or `material.*` objects. The shared
shell uses that metadata for `.tables`, `.macros`,
`.describe`, and context-aware completion; `.completion reload` refreshes it explicitly.

The Atlas web home page provides a request-scoped assistant over the same public query boundary.
The server may inspect public metadata and run row-bounded, read-only queries; it cannot access
physical or control-plane schemas. SQL drafts are accepted only after Atlas validates the
statement and binds it with `EXPLAIN`. The browser keeps a small recent conversation context
locally, while the server stores no assistant session state. During a turn, each SQL operation
exposes its purpose, elapsed time, final status, bounded rows, columns, and types. A completed turn
renders a compact Markdown answer, result tables, and validated SQL drafts. A draft can be copied
or run directly in the conversation, where its bounded result table is rendered in place. Result
tables stay within the conversation, scroll horizontally, support resizable columns and cell
copying, and can be copied or downloaded as CSV or JSON. Catalogue assistance is not a terminal
command.

## 2. Python SDK

`packages/atlas-python-sdk/` is the first client package. It wraps DuckDB connection setup and
results and control-plane operations such as crawl submission and run
tracking. It does not define public catalogue semantics or compile and rewrite user SQL.
`atlas_sdk.conn.duck()` returns an ordinary `duckdb.DuckDBPyConnection` over the versioned public
catalogue using the same `ATLAS_DUCKLAKE_*` attachment contract as Atlas. Both runtime and SDK
connections select one connection protocol at their factory boundary; filesystem, S3, and
externally configured DuckDB URI storage do not create branches in query or materialization code.

## 3. Optional native optimization

Only a measured compiler/optimizer gap that remains after performance triage justifies native
code. Atlas ships one exact-version C++ DuckDB extension containing plan policy and specialist
functions. Base catalogue queries never require it. Standards-shaped CSS selectors are an
explicit extension capability and are absent, rather than emulated poorly, when it is unavailable.

Atlas will not maintain parallel stable-C and C++ production extensions or CI paths. Atlas images
compile and package the matching extension; the SDK and direct shell load a matching host artifact
before attaching DuckLake. The DuckLake catalogue remains the authoritative interface. The local
build, direct DuckLake, and differential testing workflow is documented in
[`EXTENSION_DEVELOPMENT.md`](EXTENSION_DEVELOPMENT.md).

### Shared plan analysis and policy

The native extension has one Atlas plan-analysis boundary. It reduces DuckDB's optimized logical
plan to reusable facts about physical Atlas relations and grain, native capabilities, estimated
cardinality, expansion boundaries, blocking state, and the operator path connecting a hazard to
its consumer. An ordered policy registry consumes those facts and produces optimizer actions,
structured diagnostics, or enforced errors. Feature-specific code may contribute capabilities and
rules, but it must not install an independent whole-plan visitor.

The initial rules cover both DOM and non-DOM plans: unsafe collection of element-grain rows before
document evaluation, large Cartesian products between Atlas relations, unexpectedly broad
document operations, repeated DOM work, and unbounded blocking state over large Atlas relations.
Future rewrites must be optimizer actions from this shared policy rather than standalone
SQL-spelling patches.

The internal native table function:

```sql
atlas_lint_query(sql, profile := 'interactive')
```

accepts exactly one `SELECT` or `EXPLAIN`, binds and fully optimizes it without executing it, and
returns:

```text
severity, code, message, hint, operator_path,
estimated_rows, estimated_bytes, confidence
```

It returns warnings and would-be errors as rows so an SDK or shell can decide how to present them.
Normal execution throws only error-severity diagnostics while safety is enforced. Plain `EXPLAIN`
never throws, while `EXPLAIN ANALYZE` executes its child and remains subject to enforcement. The
session settings are:

```sql
SET atlas_query_safety = 'enforce';      -- enforce, audit, or off
SET atlas_query_profile = 'interactive'; -- interactive or batch
```

Profiles are versioned cardinality and state budgets, not wall-clock predictions. Interactive
budgets protect request/console work; batch budgets permit deliberate larger work. Hard errors
require a high-confidence unsafe shape and an exceeded budget. `estimated_bytes` is a working-state
proxy derived from estimated rows and operator shape, not an exact peak-allocation prediction. The
Atlas shells continue to validate their public namespace boundary independently; exposing lint
through those shells or the SDK does not make this internal function part of the portable `web.*`
or `dom.*` contract.

The interactive selector warning boundary is 1,000 content identities, based on the representative
development lake crossing roughly 30 seconds between 1,000 and 1,500 page-scoped documents. It is
a lint warning, not an execution ban. One individual document is hard-bounded at 1,000,000
elements to prevent an adversarial page from defeating document-at-a-time memory bounds.

### Selector query shapes

For one known page, select the current visit first and pass its immutable content identity to the
DOM operation:

```sql
SELECT *
FROM dom.query_selector_all(
    (
        SELECT content_id
        FROM web.page_visit AS visit
        JOIN web.page AS page
          ON page.latest_page_visit_id = visit.page_visit_id
        WHERE page.url = 'https://commoncrawl.org/'
          AND content_id IS NOT NULL
    ),
    'a[href]'
);
```

For comparison or discovery across pages, make the page/content scope explicit, deduplicate shared
content, and invoke the selector laterally:

```sql
WITH scope AS MATERIALIZED (
    SELECT DISTINCT visit.content_id
    FROM web.page_visit AS visit
    JOIN web.page AS page
      ON page.latest_page_visit_id = visit.page_visit_id
    WHERE page.hostname = 'example.com'
      AND visit.content_id IS NOT NULL
    LIMIT 100
)
SELECT scope.content_id, match.element_index, match.tag_name,
       dom.get_attribute(match.attributes, 'href') AS href
FROM scope
JOIN LATERAL dom.query_selector_all(
    scope.content_id,
    'article a[href]'
) AS match ON true;
```

Raise the scope limit through `100`, `500`, `1000`, then `1500` when measuring a new environment.
The limit bounds documents, while the selector operator bounds memory to one document at a time.
Lint the exact discovery query before executing it:

```sql
SELECT *
FROM atlas_lint_query($query$
    WITH scope AS MATERIALIZED (
        SELECT DISTINCT visit.content_id
        FROM web.page_visit AS visit
        JOIN web.page AS page
          ON page.latest_page_visit_id = visit.page_visit_id
        WHERE visit.content_id IS NOT NULL
        LIMIT 1500
    )
    SELECT match.*
    FROM scope
    JOIN LATERAL dom.query_selector_all(
        scope.content_id,
        'a[href]'
    ) AS match ON true
$query$);
```
