# Query

Periplus delivers its query interface in three layers, in this order.

The continuous investigation loop, decision matrix and production-reader bench are
documented in [QUERY_OPTIMIZATION.md](QUERY_OPTIMIZATION.md).

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
  Periplus cannot provide a safe execution path.
- A SQL or plan rewrite changes the physical form only when it preserves the portable public
  semantics and has differential correctness and activation tests.

Compiler behavior must not conceal a poor public schema, and schema changes must not encode around
one accidental optimizer plan. “Compiler” here means Periplus query analysis in the query API; it does not restore the removed schema-dependent Python compiler.

## 1. Portable DuckLake catalogue

Periplus initialization installs and versions the complete public interface defined in
[`SCHEMA.md`](SCHEMA.md) as persistent DuckLake views over `ingest.*` and `material.*`. These
catalogue objects define the portable public semantics and remain correct without a Periplus SDK. `periplus-setup` installs the complete catalogue or fails rather than publishing a
partial installation. The objects are changed only by Periplus catalogue upgrades.

The authoritative one-object SQL definitions live under
`packages/periplus/src/periplus/platform/catalogue/sql/public_v1/`. The explicit manifest in
`periplus.platform.catalogue.public` installs them in dependency order. `periplus-setup` replaces the
complete interface transactionally after reconciling the typed physical schema, then validates
object names, columns, view comments, and manifest descriptions. Ordinary
processes validate this contract and never repair it at startup.

Every public view and view column has a concise description derived from the semantic contract in
`SCHEMA.md`. Setup reapplies supported view comments after replacing each view. DuckLake does not
currently accept `COMMENT ON COLUMN` for view columns, so the authoritative manifest remains the
source of column descriptions for internal metadata consumers.

Recurring expensive computations belong in Periplus-owned `material.*` relations maintained through
the ordinary materialization lifecycle.

The shared terminal and web shell accepts bounded read-only SQL over qualified or unqualified relations in
the public registry only. It also accepts `DESCRIBE`, `EXPLAIN`, `EXPLAIN ANALYZE`, and `SUMMARIZE`
when their target passes the same public-namespace validation, plus `SHOW TABLES` for each public
schema. The shared shell derives `.tables`, `.describe`, and context-aware completion through that
same query operation; `.completion reload` refreshes the derived information explicitly.

The query API defaults `schema_version` to `public_v1`, rejects unsupported versions,
and selects that namespace before locking the connection. Responses report the
resolved version; saved queries should preserve it separately from `source_snapshot`.
The public contract has one versioned namespace, with no old-schema aliases.

### Query server boundary

The public app provides a landing page, SQL console and shared website coverage. The SQL console supports read-only queries and CSV/JSON export. Its optional Ask SQL assistant can execute exploratory SQL and prepares editor proposals for explicit user review. Coverage requests use the control API; crawl completion is not proof of indexing readiness.

SQL preparation and execution remain in the separate Python
`periplus-query` process, which exposes only `POST /query/prep`, `POST /query/exec`, and
`GET /query/helpers`, and its health probe. Query routes do not exist on the control API.

Both operations accept SQL and positional parameters and use the same public SQL validation.
Preparation binds and explains without executing the analytical query, returning SQL, parameters,
a query ID, diagnostics, and a plan. Compiler `public-query-v11` applies the promoted
optimizations documented below in both stable and experimental. Responses preserve
the submitted SQL and parameters and identify applied rewrites.
The execution-only row-limit wrapper remains a resource control. DuckDB performs native
optimization. The returned plan explains the selected statement; both modes identify any applied rewrite.
Execution always prepares independently, then runs the query. It does not trust a prior prep call.
Execution returns `source_snapshot`, obtained from `ducklake_current_snapshot` inside the same
read transaction before binding or executing the query. The result and snapshot therefore describe
one consistent source even if another writer commits concurrently.
The browser renders server results; there is no Wasm runtime, metadata export, file proxy, or
client telemetry endpoint. Server logs capture generated operation IDs, safe outcomes, truncation, row counts and elapsed time. SQL text, parameters, plans and result rows are not logged.

Each query process owns one read-only DuckLake connection and accepts one operation at a time.
Excess requests return 429 with Retry-After; there is no queue. Operator-configured duration, row and result-size limits default to 20 seconds, 1,000 rows and 8 MiB.
Admin Query limits permits 1–600 seconds, 1–10,000,000 rows and 1–1024 MiB.
Input budgets default to 4 MiB and 100,000 parameter values, with ceilings of 16 MiB
and 1,000,000 values (including collection containers). SQL text has a 1,000,000-character
structural bound; the request-byte budget also includes all parameters. These apply to preparation
and execution through this service, including browser, SDK, assistant, browsing and corpus-seed
clients. Existing requests retain their starting limits; new requests read the current policy.
Row and byte limits truncate results explicitly; duration interrupts the operation and returns 408.
The duration starts at SQL preparation and excludes the bounded policy HTTP lookup.
Stream execution includes backpressure and delivery, with a one-second transport cleanup allowance. A default 512 MB DuckDB memory budget and 256 MB spill budget also bound execution.
Memory, spill and worker concurrency remain deployment-owned settings. Deployment-owned `PERIPLUS_DUCKDB_THREADS`, `PERIPLUS_DUCKDB_MEMORY_LIMIT`
and `PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE` override connection sizing before
configuration is locked; Helm exposes them under `query.duckdb`. Each replica
still admits one operation. These capacity settings do not change SQL semantics,
credentials or the operator-configured execution limits. Result truncation is explicit. Integers outside JavaScript's safe range and decimals
are returned as strings; SQL column types accompany results. Streaming disconnects request cancellation and interrupt the admitted connection;
the admission slot remains occupied until execution and cleanup stop. Buffered requests
remain bounded by their server deadline.
An internal/fatal DuckDB error or failed transaction cleanup discards the connection. Cleanup does
not replace the original query failure. The next admitted request creates a fresh attachment with
the same read-only credentials and locked configuration; failed SQL is not automatically replayed.
The health probe returns 503 while a connection is discarded, and returns 200 after recovery.

The process receives only its query-service token, DuckLake metadata reader credentials, and
lake object-reader credentials. No control Postgres, NATS, raw repository, or writer credentials
are composed into this process. DuckLake attaches READ_ONLY. After attachment, external access
is restricted to the configured lake data prefix, extension autoloading is disabled, and the
DuckDB configuration is locked. Public semantics and DuckDB's ordinary optimizer remain available.

Storage permissions are an independent boundary: the PostgreSQL role has SELECT on metadata
and future metadata tables, and the S3 identity has GetObject on lake objects only. Production
infrastructure owns these grants, resource isolation, and ingress rate limits. A read-only
attachment alone is not a substitute for read-only credentials.

The query process reads `GET /access` on the control API through one process-owned HTTP client,
using its query-service token, after acquiring its sole admission slot. This token grants public
settings reads and query-history appends only. A five-second lookup failure rejects the query with
503 `access_unavailable`; no stale/default policy is used and no lake query executes. Helpers are
metadata-only and do not require this lookup. SQL request bodies cannot override limits.
Apply Alembic revision `20260908_0010` before deploying this contract; it adds the defaults to the
existing JSON policy and increments its optimistic version without resetting rate windows.
The public query gateway allows 610 seconds; the Python SDK defaults to 620 seconds. Callers may
impose shorter deadlines, including bounded agent runs and crawler selections. These do not
increase the server's limits. Administrative SQL retains its separate fixed result limits.

### Page discovery and structured text

Use `search('query')` through the query API for positional page discovery, and
`html_element.text` / `html_node.text` for deterministic structural SQL. Search
returns content IDs, matched snippets with node IDs, and scores; see
[the contract](SCHEMA.md#public_v1searchquery). It is the deliberate API-only
exception to portable catalogue SQL: the stored macro supplies a typed signature
and explicitly rejects direct execution. ICU query tokenization and staged lookup
belong in `query/search.py`, not an extension or a materialization callback.

Vocabulary and one positional posting relation remain private; prose is not
materialized. All parsed text-node locations are indexed, excluding attributes and
comments. Arbitrary SQL text predicates are not transparently rewritten. This is
schema/catalogue and API-contract work, not a semantics-preserving optimizer pass.
Search resolves only constant literal/parameter calls, binds the outer SQL before
running discovery, and shares its snapshot, deadline, cancellation and result limits.
Preparation does not run discovery. Plain and phrase modes are explicit; no public
term surface or DISTINCT-term optimization is introduced.

## 2. Python SDK

`packages/periplus-python-sdk/` provides synchronous `Client` and asynchronous `AsyncClient`
HTTP clients for the public application's `/api/query/prep`, `/api/query/exec`, and
`/api/query/helpers` routes. Callers configure the public application URL directly or through
`PERIPLUS_PUBLIC_URL`; they receive no service token or lake credentials. The SDK exposes only
read-only catalogue queries, preparation and helper discovery, with typed JSON responses.
It has no direct DuckDB attachment, collection mutations or administrative controls.

The public gateway applies the same SQL admission policy as the browser and injects its internal
query token. It accepts only `sdk` as an alternate query-source label, otherwise using
`public_console`; this label is caller-asserted analytics, not authentication or entitlement.
Preparation and execution flow through the query service's validation, diagnostics and optimization
boundary. Execution always prepares independently. Query history records SDK operations through
the existing best-effort history path; no separate SDK recorder exists. Result types, source
snapshot, diagnostics and truncation are preserved. Clients never retry or paginate automatically.
Direct operator lake access remains available through standard DuckDB and `./ducklake.sh`.

## 3. Query API optimization

The query API owns validation, diagnostics, and future semantics-preserving SQL optimizations.
Periplus uses standard DuckDB and does not ship a custom query extension. The native DuckLake CDC
extension belongs only to live materialization and does not participate in query preparation.

Before adding an optimization, follow the performance triage above and record representative
plans and scale. Verify equivalent column types, values, multiplicities, and required ordering
at the same snapshot. The existing benchmark runner and query cases remain useful for this work.

## SQL helper development

### SQL workspace assistant

Ask SQL explores the public corpus with a read-only SQL tool calling `/query/exec`.
A separate `SUGGEST_SQL` tool proposes complete editor contents without applying them.
It receives editor context, up to 20 history messages, the public schema and installed helpers.
Each generation allows up to 32 steps and 16,000 output tokens per model call, within a
300-second request deadline. Tool output includes up to 100 rows and 60,000 serialized
characters, with explicit sampling, truncation, query identity and source snapshot.
Service failures stop further tool execution for that turn. Query-service limits still apply.
SUGGEST_SQL prepares proposals before returning tool feedback, allowing the model to correct invalid SQL before answering. Direct final-output proposals receive preparation with one repair attempt. Activity streams to a collapsed, expandable group; answers render Markdown.
Applying remains explicit and does not execute the proposal. Up to two assistant requests
run concurrently per public process. Conversation and undo state remain in the browser.

### Catalogue helper declarations

The explicit helper registry is `packages/periplus/src/periplus/platform/catalogue/helpers/`.
Its README documents the add/edit/test/install workflow. Each declaration provides a SQL
resource, signature, output descriptions, limits, and executable examples. The public manifest
installs the macros alongside views; `GET /query/helpers` derives documentation from that same
manifest. Next.js proxies discovery and loads it into the agent context for each request.
There is no helper-specific Python execution or prep-time rewrite path.

The initial `public_v1.subtree_text` helper addresses a catalogue usability/correctness gap:
callers were reconstructing DOM text incorrectly. It is not an optimizer workaround. The
implementation orders text nodes by document position within the exclusive subtree boundary,
excluding comments and text outside the root. Selected node count and output characters
are bounded; ordinary query limits still govern physical scan cost. Contract tests compare every
subtree with parser text, exercise lateral calls, and install the helper into read-only DuckLake
query-service fixtures. Future performance changes follow the triage above.

### Query failure categories

Query failures return a safe `code` alongside `detail`: `sql_invalid` (422), `helper_limit`
(422), `resource_limit` (408 for time or 422 for memory), `service_busy` (429),
`storage_unavailable` (503), `access_unavailable` (503 for unavailable execution policy), or `query_failed` (500). Native DuckDB errors never expose
storage URLs or credentials. HTTP and filesystem failures are operational; clients must
not treat them as evidence of missing corpus coverage or repeatedly rewrite SQL to fix them.
The Next.js agent preserves these categories and stops querying when storage/service access
is unavailable. Read-only clients never initiate lake repair.

### Corpus seed selections

The crawler calls `POST /query/exec` through one process-owned bounded HTTP client for an explicit
collection seed query. It supplies frozen SQL and positional parameters and accepts only a complete
single `url` column. Truncated results, invalid URLs, and oversized selections fail visibly rather
than admitting a silently incomplete seed set. Service/storage failures defer selection with bounded
backoff; page-local follow selection and existing acquisitions continue independently.

Before admitting any seed, the crawler freezes the candidates, query ID, source snapshot, and
selection time. Resumption uses that checkpoint. Collection outcome evidence retains the provenance
and a digest of the frozen candidates; the collection definition retains SQL and parameters.

### Scheduled exploration

Scheduled requests reevaluate ordinary bounded seed SQL. The former background
historical-check client and its benchmark script have been removed with that
execution path. Query performance triage and public query limits still apply.
See [SCHEDULES.md](SCHEDULES.md) for cadence and execution semantics.

### Historical collection browsing

`GET /collections/history` reads immutable collection definitions and any arrived terminal outcomes
through the control API's existing catalogue owner. It returns at most 100 summaries per page;
`next_cursor` orders subsequent pages by definition time and collection identity, descending.
Public and administrative callers browse the same shared lineage and request definitions. Missing outcomes retain unknown counters rather than zeroes.
Responses carry `source: history` and `as_of`; `GET /collections/{id}` supplies the full frozen intent.

This is a live append-only feed, not a snapshot pinned across pages. Definitions ingested above an
already-consumed cursor appear on refresh. Each read is bounded by a ten-second interruption deadline,
and historical list/detail requests share one outstanding-read slot. Invalid cursors return 422;
busy, unavailable, oversized, or inconsistent historical data returns retryable 503. Reads do not
recreate current execution or imply verified materialization readiness.

### Historical observation provenance

`GET /frontier/observations/{id}/lineage` returns committed capture causes and request result uses
as distinct records. A later reuse is a fulfillment, never an added cause for the original capture.
The read uses immutable observation and lineage evidence, so retirement of control rows does not
remove it. Every observation and parent observation belongs to the shared corpus. Fulfillments
require a committed collection definition, independent of its request class.

Pages contain at most 100 bounded metadata records and use descending decision-time, record-ID,
and record-kind cursors bound to the observation. Reads share the bounded
catalogue owner and ten-second interruption deadline. Pages are not a pinned snapshot: later commits
may appear above an existing cursor, so callers refresh the first page to see them. Missing visible
observations return 404; catalogue unavailability returns retryable 503. Empty visible lineage may
mean ingestion lag and is not a proof that no other relationships exist.

### Anonymous parameter casts

The query validator recognizes DuckDB's adjacent anonymous-parameter cast syntax (`?::UUID`,
`?::INTEGER`) as a parameter followed by a cast. SQLGlot 30.12.0 inherits a shared `?::` operator
keyword that prevents this recognition in its DuckDB dialect. Periplus's query-boundary tokenizer
excludes that keyword; all other DuckDB lexical and parser rules are inherited. Original SQL and
parameters execute unchanged. Real DuckLake tests compare cast forms and types and retain the
single-statement and public-namespace restrictions. This is a parser correction, not a public schema
change or an execution-plan rewrite.

### Privileged admin console

The admin console at `/` sends `POST /admin/sql/exec` to the control API with its
server-side administrative credential. Public credentials cannot access this route.
This is an operator escape hatch: it executes DuckDB SQL directly against a writable
DuckLake attachment, including internal schemas, metadata queries, DDL and DML.
It does not use public query validation, preparation, rewriting, or read-only credentials.

Each API process admits one administrative SQL request at a time (otherwise 429).
Each request opens a dedicated writable connection through the standard factory and closes
it on completion. A script runs in one transaction; failure rolls back its database changes.
Explicit transaction statements are rejected before execution. Positional parameters are
supported for a single statement. The response contains the final statement's result, capped
at 1,000 rows and 8 MiB, with explicit truncation; truncating output does not undo writes.
Requests are capped at 512 KiB and SQL at 100,000 characters. Temporary objects and settings
do not persist between requests. This endpoint does not impose the public query deadline.
External effects of commands such as COPY are not transactional; commands that DuckDB
does not allow within a transaction fail. A lost response does not prove rollback, and
clients must not automatically retry writes.

Cloudflare Access protects the admin UI and its complete API gateway in production.
The isolated public query service retains its read-only credentials and namespace restrictions.

### Private query execution history

[QUERY_HISTORY.md](QUERY_HISTORY.md) defines the 30-day private `query_executions`
table in control Postgres, bounded best-effort recording, janitor cleanup and the
`observatory/queries` dashboard. This is explicitly approved product analytics;
no query results or crawl history are added to control Postgres.

### HTML table view validation (2026-09-08)

The first html_table_cell view shape had an optimizer/predicate-pushdown issue:
correlated text extraction and span expressions admitted unrelated content scans,
despite a query selecting one content and one table. The public cell grain was
already bounded to the chosen table and did not require corpus-wide layout.
The SQL view now uses filterable grouping keys, inlined row/cell inputs, ordinary
text joins and a scalar list fold for grid placement. No query API rewrite,
physical relation, or materialization was introduced.

Representative query:

```sql
SELECT * FROM public_v1.html_table_cell
WHERE content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'
  AND table_node_index = 165
ORDER BY row_index, column_index;
```

Read-only local-lake validation at snapshot 7527 used 5,448,418 node rows and
2,977,143 element rows. The selected book-details table returned 14 source cells.
The initial plan admitted 2,629,871 element rows from an unrelated-content scan.
The revised EXPLAIN ANALYZE plan places the exact content hash in every primitive
scan, emits 35 text nodes, 7 source rows, 14 source cells and one table into their
respective operations; the row-parent scan emits 211 elements of that content.
Each primitive scan read two Parquet files.
Observed local execution was 0.15–0.24 seconds (warm/cache-sensitive), versus about
1.05 seconds for the first shape. This is evidence for filtered on-demand use,
not a billion-row scalability or unfiltered-corpus performance claim. DuckLake's
profile rows-scanned counter exceeded the source row count, so it is not used as
a physical I/O measure here. Reassess with production file counts and workloads
before deciding whether to materialize.

Fixtures verify that content/table filters isolate malformed unrelated tables,
while filtering output rows still accounts for preceding row-spanning cells.

A separate synthetic 100-row by 10-column table returned all 1,000 expected cells
in 0.20 seconds on a local in-memory fixture. Local deployment verification
confirmed both public DESCRIBE contracts and the same 14-cell book table through
the public HTTP query gateway. Public documentation includes the relations and
parameterized extraction example.

### Heading view validation (2026-09-08)

The html_heading view uses an ordinary content-keyed subtree join and ordered text
aggregation. No schema or optimizer performance defect was observed in the initial
filtered validation, and no compiler rewrite or physical materialization was added.

```sql
SELECT node_index, level, text FROM public_v1.html_heading
WHERE content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'
ORDER BY node_index;
```

The local book page returned 10 headings in 0.24 seconds. EXPLAIN ANALYZE showed the
exact content predicate on both primitive scans, each reading two Parquet files.
The scans emitted 228 text nodes and 211 elements from that content before the
heading/subtree filters and aggregation. This validates the filtered query shape;
it is not a guarantee for unfiltered corpus queries or billion-row deployments.

### Metadata view validation (2026-09-08)

Initial filtered validation found no schema or optimizer performance defect.
The original html_metadata used inlined primitive scans and UNION ALL, with an
ordered text join only for titles. No compiler rewrite or materialization was added.
Selecting the book content hash used in the heading example returned 12 metadata
rows in 0.21 seconds locally, including its title, description, language and raw
relative stylesheet/icon URLs. EXPLAIN ANALYZE showed the exact content predicate
on each primitive scan. This validates filtered on-demand access, not unfiltered
corpus performance. Declaration, null/empty, repetition, multi-token rel and
foreign-namespace behavior are covered by real HTML parser fixtures.

The [title direct-text investigation](query-investigations/title-direct-text/README.md)
uses the stored HTML title text to remove that reconstruction without changing
metadata declarations or adding a compiler rewrite.

### Image view validation (2026-09-08)

html_image is a direct projection/filter over html_element. Initial filtered
validation found no schema or optimizer performance defect; no rewrite or
materialization was added. Selecting the book content hash used above returned
seven images in 0.05 seconds locally. EXPLAIN ANALYZE showed exact content and
img predicates on the single primitive scan, emitting seven rows and reading two
Parquet files. URLs remained relative and undeclared dimensions remained null.
This validates the filtered shape, not unfiltered corpus-scale performance.

### JSON-LD view validation (2026-09-08)

Initial filtered validation found no schema or optimizer performance defect.
The original html_jsonld implementation joined selected script nodes to their
direct text children and used TRY_CAST to retain parser failures as rows. It adds no compiler rewrite or
physical materialization and does not depend on the private JSON-LD projection.

```sql
SELECT node_index, value, parse_error FROM public_v1.html_jsonld
WHERE content_id = '072a5a77e6bf9d591a30832addace1b20ccfa7e4e13b1bd9a00a3779b7bce252'
ORDER BY node_index;
```

A captured Probot article returned one complete @graph document without a parse
error in 0.17 seconds locally. EXPLAIN ANALYZE placed the exact content predicate
on both primitive scans, each reading two files and emitting one row. This is
filtered-query evidence, not a guarantee for unfiltered corpus workloads.
Fixtures cover complete arrays/graphs, repeated scripts, invalid/empty declarations,
JSON null, type matching and foreign namespaces.

The follow-up [direct-text investigation](query-investigations/jsonld-direct-text/README.md)
removes that redundant reconstruction using existing `html_element.text_direct`.
The public columns and parse semantics remain unchanged.

### List view validation (2026-09-08)

Initial filtered validation found no schema or optimizer performance defect.
html_list counts direct items for reversed defaults; html_list_item uses ordered
windows for numbering resets and subtree joins for text. No compiler rewrite or
materialization was added. Selecting the book content hash used above returned
10 list items in 0.18 seconds locally. EXPLAIN ANALYZE put the exact content
predicate on every primitive scan, each reading two files. The text scan emitted
276 nodes, direct-item scans 10 rows each, and list-candidate scans 211 elements
before tag filtering. This is filtered-query evidence, not a corpus-wide guarantee.
Fixtures verify that filtering later output items preserves earlier value resets.

### Form views validation (2026-09-08)

The initial form-control ownership query had an optimizer predicate-pushdown
issue: the selected content was bounded, but conditional outer joins admitted
574,409 ID-bearing elements and 4,572 forms from unrelated content. Splitting
explicit-reference and ancestor ownership into disjoint UNION ALL branches
preserved the public semantics and let DuckDB push content predicates into every
scan. This was an optimizer issue, not a reason to materialize the relations.

For content 01c41839fd99a13cc60ada13d53a98fb5ad75fa65f70f64edae0acb5ee1eba61,
`SELECT * FROM public_v1.html_form_control WHERE content_id = ? ORDER BY node_index`
returned 12 controls in 0.25 seconds locally (initial shape: 0.77 seconds).
EXPLAIN ANALYZE showed each primitive scan reading two files; the ID scan emitted
18 elements and the form scan one form. Option extraction for content
072a5a77e6bf9d591a30832addace1b20ccfa7e4e13b1bd9a00a3779b7bce252 returned four
options in 0.09 seconds, with content predicates on all scans and two files each.
These timings validate filtered local queries, not billion-row corpus workloads.
No query API rewrite, stored projection or form execution was added.

### Section view validation (2026-09-08)

Initial filtered validation found no schema or optimizer performance defect.
html_section computes preceding-parent and following-boundary windows over
HTML headings, with the document-root node providing the final boundary. It
adds no stored projection or compiler rewrite. Selecting the book content hash
used above returned 10 passages in 0.11 seconds locally. EXPLAIN ANALYZE put the
exact content predicate on both primitive scans, each reading two files: one
root node and 211 element candidates before heading filtering. Product Description
was [154, 161), ending at Product Information; its parent was heading 112.
This validates filtered local use, not unfiltered corpus-scale performance.
Fixtures verify that a heading filter retains the surrounding headings needed
to compute its parent and end, plus all six ranks and empty passages.

### Link sort-order comparison (2026-09-08)

Classification: potential physical-layout mismatch, not an established optimizer
failure. Public joins select links by capture identity, whereas stored links lead
with source URL. No schema, compiler rewrite, or sort-order change was made.

Exported all 141,612 current link rows (2,100 visits) once and wrote two local
Parquet files with identical columns and 16,384-row groups: current order
`source_url, target_url, observed_at, occurrence_id` and candidate order
`visit_id, element_index`. The current corpus fits a single observed month;
this experiment isolates ordering, not multi-month partition pruning or remote I/O.
The files were 6,990,818 and 9,622,669 bytes respectively.

A paired warm-cache comparison used one DuckDB thread, 20 deterministic random
samples per workload (Python random seed 42 over sorted distinct identities),
one warm-up and five measured repetitions, alternating order each repetition.
Queries returned `count(*), sum(length(target_url)), sum(element_index)`;
results matched for both layouts. Medians in milliseconds:

| Predicate | Current source order | Candidate visit order |
| --- | ---: | ---: |
| One capture | 0.608 | 0.693 |
| Ten captures | 3.817 | 5.823 |
| One source URL | 0.574 | 0.688 |

A separate full-row extraction comparison also checked equal returned rows.
These small local measurements do not justify a rewrite or establish billion-row
performance. Retain the current layout. Revisit with larger, multi-month data,
remote file/row-group pruning evidence, and representative capture joins before
changing it. Millisecond differences should not be interpreted as production SLAs.

Structure-first discovery (for example, finding salary tables or phone-number
form controls before identifying their pages) remains valid public SQL. Content
hash partitioning and content/node ordering optimize document-local extraction;
they do not promise pruning for text, tag, or attribute predicates across the
corpus. Measure a recurring reverse-discovery workload before adding a feature
projection or changing the common primitive ordering.

A structure-first diagnostic selected distinct content IDs from
`html_form_control WHERE tag = 'input' AND lower(type) = 'tel' AND required`,
then joined captures. It reached the public 20-second deadline during a concurrent
rebuild. The corresponding primitive attribute search finished in 2.127 seconds
and returned no rows. EXPLAIN retained the form view's owner-resolution joins and
window despite ownership not being selected (12 join nodes versus 3 in the
primitive query, including capture expansion). This is evidence of avoidable
view/optimizer work, not evidence that hash partitioning must change. Timing was
not isolated; investigate projection/join elimination before considering stored
form projections. No rewrite or physical change was made for this diagnostic.
A repeat of the primitive query with the explicit HTML namespace predicate took
14.811 seconds under rebuild load, again returning no rows. This reinforces that
the timings are contention-sensitive; the retained unnecessary owner joins in
EXPLAIN, rather than the timing ratio, motivate the optimizer investigation.

The physical cleanup rebuild additionally exposed per-identity retention-fence
round trips: a worker stack sample was inside `retention.identities.touch` during
commit. This is writer execution overhead, not a reason to alter the public
schema or node/link ordering. Fence reads and writes now use set-based batches of
the same identities, preserving validation order, revision increments, and the
same transactional write conflicts. Tests cover mixed creation/update, duplicates,
missing/retired identities, and existing retirement races. The local rebuild was
restarted with 50 visits per batch after the 500-visit run encountered high memory
use and a worker restart; no default batch-size or physical partition change was
made. Shared-host load and swap also affected the rebuild, so its elapsed time is
not an isolated layout benchmark.

### Depth rebuild performance investigation (2026-09-08)

Classification: observed writer/coordination overhead and host contention; no
public-schema or HTML node sort-order defect established. Run
edb7f64a-c96d-47b2-b8e9-23d85c076793 uses 213 ten-visit batches. This smaller
operator-selected batch size reduces peak memory but multiplies fixed work.

An early scrape across four materializers covered 55 committed batches: mean
recorded preparation was 10.4 seconds, Parquet writing 5.9 seconds, and successful
commit 6.8 seconds. Workers reported 167 transaction conflicts and 17 outer retries.
These are cumulative concurrent-worker measurements, not additive wall time. The
phase metrics exclude connection setup, projection-to-Arrow conversion (currently
between timers), failed commit attempts, and backoff. Preparation includes source
SQL and raw-object reads, not just parsing.

A read-only probe over ten documents (374,378 source bytes) measured catalogue
connection setup at 7.34 seconds, visit selection at 5.99 seconds cold / 3.39 warm,
and source resolution at 34.44 seconds cold / 5.59 warm. Source resolution on this
already-committed batch skips new-content ownership lookup, so it does not exactly
reproduce an uncommitted batch. Measurements were under concurrent rebuild/build
load: host load average around 38 and swap usage about 4.5 GB.

A short worker profile frequently sampled retention identity SELECT/UPDATE, plus
source selection and Parquet writes. Sampling fell behind schedule under load, so
it supports locations, not precise CPU percentages. The active guard table had
16 data files (325,940 bytes) and 31 delete files (57,123 bytes); visits had two
files, documents 48, and the old active HTML node/element tables eight each. These
counts do not by themselves prove a file-layout problem.

A bounded one-replica experiment retained the normal two writer lanes. Over about
121 seconds it progressed from 62 to 70 completed batches and the surviving
worker's conflict counter rose from 46 to 51. Four replicas were restored. Mixed
batch sizes in bytes, changing host load, and other writers prevent treating this
as a controlled throughput comparison or evidence that one replica is optimal.

Next investigations should account for complete batch wall time, classify actual
transaction conflict reasons, measure reusable per-lane connection/cache benefits,
and balance batch bytes against per-file registration overhead. Each batch opens
a fresh catalogue connection, emits one file per populated projection partition,
and registers files one at a time inside the transaction. Do not change public
relations or node ordering to conceal these writer costs.

Later metadata-Postgres logs identified concrete concurrent-commit collisions:
`ducklake_snapshot_pkey` violations for snapshot IDs 8162, 8163, 8164, 8168, and
8170 around 12:50–12:51 UTC. These are DuckLake snapshot allocation conflicts,
not duplicate Periplus retention identities. They establish that not all retry
cost can be attributed specifically to guards. Moving operational state out does
not eliminate native lake commit contention; batching and writer concurrency
still matter. The frequency alone does not establish an upstream defect.

### Operational-state cutover measurements (2026-09-08)

Classification: catalogue/storage write design, not a public SQL optimizer issue.
The four Periplus bookkeeping data tables were removed after their durable state
was transferred to control Postgres. Generation commits now use exact Postgres
claims and deterministic derived-identity replacement; there is no SQL rewrite
intended to hide the previous bookkeeping cost.

Replacement rebuild `31cf5645-c380-472a-8e1d-9d5d4ab3edf4` uses 50-visit batches.
It reached 40/43 batches (1,973 visits) in roughly three minutes. At a later sample,
the surviving worker metrics covered 36 completed batches: zero transaction
conflicts, 147.247 seconds total commit phase, averaging 4.09 seconds per batch,
including claim acquisition/waiting. These are not a controlled comparison with
the earlier 10-visit sample (55 completions, 167 conflicts, 6.8-second mean
successful commit): batch composition, host load and worker lifetimes differ.

The final two large batches required retries and substantial preparation time.
NATS logged 5–11-second control-request delays around 13:23:53 UTC; ingestor
observation tasks subsequently timed out and the processes restarted. A materializer
also restarted. Do not attribute all elapsed-time differences to the Postgres move,
or infer that removing lake bookkeeping solves parser memory and batch-size costs.


The replacement run completed and activated in 632.73 seconds (10m33s), with
44 batches including catch-up, 2,124 visits and 8,728,033 rows. This is a modest
wall-clock improvement over the earlier roughly 12-minute 50-visit run, not a
controlled speedup claim. The largest batches dominate the tail despite the
reduction in commit contention.

The new commit protocol performs identity-scoped replacement of derived rows on
each batch, then records its receipt in Postgres. That is a deliberate correctness
trade-off for safe replay across the two stores. This small corpus does not prove
its billion-row cost: content predicates match the HTML sort key, while visit-ID
replacement on link occurrences still deserves partition-pruning measurements
before freezing a large-scale physical layout. Public relations are unchanged.

### Projection allocation optimization (2026-09-08)

Classification: materialization runtime allocation/lifetime overhead, not a
public schema, partitioning or SQL optimizer defect. HTML projections now feed
Arrow in 8,192-row chunks instead of constructing whole-batch Python row lists.
Flat node fields are copied explicitly instead of recursively applying
`dataclasses.astuple`; element attribute dictionaries are passed directly to
Arrow rather than duplicated into lists of pairs. Preparation writes and releases
one projection's Arrow table at a time. DOM traversal skips redundant leaf
boundary replacement and unlinks finished parser nodes after copying their
values; children are already unlinked, keeping cleanup shallow. HTML5 parsing,
row identities, schema, partitioning and sort order are unchanged.

Read-only replays of batches 21 and 22 from run
`31cf5645-c380-472a-8e1d-9d5d4ab3edf4`, snapshot 8246, used the same local
2-CPU/4-GiB container bounds, 2 DuckDB threads and 2-GB DuckDB memory limit.
Parquet was written to temporary local files and never registered or uploaded.
The baseline retained all projection outputs; the revised replay used the new
one-projection-at-a-time preparation order. RSS was sampled every 100 ms.

| Measurement | Batch 21 before → after | Batch 22 before → after |
| --- | ---: | ---: |
| All projection row/Arrow construction | 3.86 → 1.64 s | 8.12 → 2.79 s |
| Node projection row/Arrow construction | 1.96 → 0.42 s | 2.92 → 0.72 s |
| Total preparation replay, including local Parquet | 27.15 → 22.12 s | 46.97 → 32.78 s |
| Sampled peak process RSS | 1.64 → 1.22 GiB | 2.13 → 1.45 GiB |
| Output rows (unchanged) | 982,959 | 1,549,516 |

These are single local replays with warm infrastructure, not production sizing
recommendations, repeated-trial medians, or end-to-end speedups. Uploads, lake
commit/claim waits and concurrent rebuild contention are excluded. The registry
implementation digest changes, so deployment requires the normal complete
materialization rebuild; no public schema version change is needed.

Validation compared all 50 source documents in batch 22 against the pre-change
implementation: every node field, element record and node/element Arrow table
matched exactly. Unit fixtures cover chunk boundaries, empty typed outputs,
attribute maps, nulls and leaf subtree boundaries.

### Single-construction DOM records (2026-09-08)

A follow-up runtime optimization reserves preorder positions when entering a
node and constructs each final immutable node/element record when leaving it.
This removes the remaining `dataclasses.replace` calls and the temporary element
lookup dictionary. Completed records retain the same order and complete shared
parse representation for every projection. Parent direct text is copied before
unlinking the parent; already-unlinked text children retain their data.

Paired replays used the same snapshot, batches and container bounds as above,
with the immediately preceding allocation optimization as the baseline. Order
was after/before for batch 21 and before/after for batch 22. Each variant ran
alone, used temporary local Parquet and did not upload or commit data.

| Measurement | Batch 21 before → after | Batch 22 before → after |
| --- | ---: | ---: |
| DOM traversal/record construction, excluding parsing and normalization | 4.41 → 2.68 s | 5.82 → 4.09 s |
| Total preparation replay | 23.13 → 20.11 s | 31.37 → 29.85 s |
| Sampled peak RSS | 1.20 → 1.22 GiB | 1.44 → 1.42 GiB |

Record construction took 30–39% less time; observed total preparation took
5–13% less time. Peak memory was essentially unchanged. These are single paired
local measurements, not production throughput or repeated-trial estimates.

Exact comparison against the preceding implementation passed for all 50 source
documents in batch 22: every node field, element record and both HTML projection
Arrow tables matched. A nested mixed-content fixture additionally checks preorder,
subtree boundaries, attributes and parent direct text after child cleanup.

### Prose deployment validation (2026-09-09)

The prose rebuild exposed a parser-disposal defect on captured HTML with both
`lang` and `xml:lang` on the root. html5lib's minidom tree can retain both qualified
attributes while sharing a local-name lookup key. Element cleanup then attempted
to delete that key twice. After copying immutable records, parsing now detaches
attribute owners before disposing the complete element. A minimal regression
fixture and the actual failing capture both parse successfully. This is parser
cleanup, not a change to public text or attribute semantics.

The worked comparison under `docs/examples/prose/` searches captured book product
pages for `robot`, then extracts titles and URLs. Initial correlated ancestor
exclusion exhausted public memory/spill limits. The final baseline uses ordered
subtree-end windows and explicit source scope instead. These are hand-written
public SQL examples, not an installed compiler rewrite; the prose materialization
removes reconstruction work directly. See the example README for validation.

### Requested-key propagation investigation (2026-09-09)

[The investigation](query-investigations/key-domain/README.md) classifies selective
joins to grouped/windowed derived relations as an optimizer problem and records
actual plans, corpus scale, nine differential cases, warm paired measurements,
and a standalone non-HTML reproduction. Propagating complete selected key domains
into inputs reduces aggregation/window work, but elapsed-time gains and file
pruning are not universal. Prep requires bound lineage, partition-locality proofs
and conservative plan selection before this can become a general automatic pass.
That investigation did not activate a runtime rewrite. The reviewed, deliberately narrower
implementation below follows the subsequent scope decision; materializations remain unchanged.


### Historical content-scoping experiment (2026-09-09; removed from API 2026-09-10)

The following records the former implementation, not current API behavior.
Compiler `public-query-v4` removes this rewrite, installed-definition checks and the
additional JSON-plan inspection from prep and execution. The content-scoping code
and plan detector were retained as research-only benchmark candidates, with differential
tests. The later narrowly scoped experimental activation is documented below. Validation, Cartesian-product
warnings, plan preview truncation, resource limits, history and native DuckDB
optimization remain. Future activation requires the optimization playbook's evidence.

### Former reviewed implementation

This is a **compiler/optimizer** fix for selective discovery followed by extraction that
DuckDB otherwise computes across the corpus. It adds no persistent relation or extraction
semantics. The submitted query is bound first. For eligible queries, prep selects the
filtered driver once in a statement-local materialized CTE, derives distinct content IDs,
and semijoins those IDs into both complete node and element inputs of reviewed views.
Final joins retain their original multiplicity; driver predicates move into selection
and other predicates remain in the final query. This lets DuckDB drop unused prose text
after discovery instead of retaining it to repeat the same test. In particular, selecting a
heading never removes the other headings needed to compute its section boundary.

Eligibility is intentionally syntactic and bounded, rather than a general lineage engine:

- A source in FROM or any JOIN is `prose`, `capture`, `html_node`, or `html_element`,
  with at least one qualified source-only WHERE conjunct. Prep selects the first eligible
  filtered source in written order and preserves the original join order. Supported expressions include comparisons,
  LIKE/ILIKE, IN lists, Boolean combinations, and lower/upper/coalesce; arbitrary
  functions and explicit casts do not qualify.
- Up to eight ordinary inner joins connect registered sources by exactly
  `USING (content_id)` or a required qualified equality to an earlier source's
  `content_id` within the `ON` conjunction. Parentheses and either equality direction
  are supported. Additional deterministic predicates remain in the original join;
  an equality appearing only inside `OR` does not establish connectivity.
  Sources must be one of the drivers or a reviewed content-local view.
- Extraction coverage is `html_node`, `html_element`, `html_metadata`, `html_heading`,
  `html_section`, `html_form`, `html_form_control`, `html_select_option`, `html_list`,
  `html_jsonld`, `html_image`, `html_code`, and `html_table`.
- User subqueries, CTEs, aggregates, windows, outer joins, explicit LIMIT/OFFSET,
  computed outputs without aliases, and existing named/numbered parameters stay unchanged.
  Anonymous `?` parameters retain their original positions through numbered references.
  Table-cell extraction and list-item numbering remain outside coverage because their
  extraction rules can raise data-dependent errors.

`CatalogueObject.content_local` is an explicit reviewed promise, not an inference from
column names. Changes to an opted-in view require reviewing that promise again. All
expanded public view definitions, primitive views, and the driver must match the installed
catalogue, normalized with DuckDB's native parser in the execution transaction. A mismatch
leaves the original query unchanged. Generated SQL passes public namespace validation too.
There is no analytical discovery query during prep, no cross-request cache of matches,
and no separate snapshot or new setting. Expansion is capped at 1,500 input AST nodes,
eight dependency levels, and the existing 100,000-character SQL limit.

The `content_scope` informational diagnostic identifies activation. Prep and execution
both apply the same rule independently. The returned SQL is standalone public SQL with
query-local CTEs; direct DuckDB users retain the original portable views and can also run
that returned SQL. Inspection statements such as EXPLAIN remain unchanged; use the prep
response to inspect the automatically chosen plan.

Validation covers all 13 opted-in views using real parser/projection fixtures at selective,
empty, and full domains in both join directions, plus all six prose/capture/heading
join orders, duplicate captures and driver nodes, complete section
partitions, multiple extraction targets, parameter order, output labels/types, definition
mismatches, unsupported syntax, and the locked read-only QueryService. Nineteen additional
read-only comparisons on corpus snapshot 8458 preserved complete result multisets and
column descriptions. For `%wild robot%`, title groups fell from 1,478 to 8, and heading
extraction/section windows from 39,457 to 91. For `%robot%`, these fell to 289 and 7,968.
The title query retains `name = 'title'`, including metadata declarations bearing that
name, and returned the same 334 rows. A subsequent read-only comparison on snapshot
8458 checked all six prose/capture/heading join orders for `%wild robot%`, including
`FROM html_heading h JOIN capture c USING (content_id) JOIN prose p USING (content_id)`.
All returned the same 182 rows and reduced heading extraction from 39,457 to 91 groups.

This rule reduces work behind extraction barriers; it is not a cost model or a guarantee
of file pruning or lower latency. Full-domain searches retained full extraction work and
added CTE/key-set overhead. Single paired timings are recorded in the investigation and
must not be treated as production performance guarantees.

### Historical shared-input plan diagnostics and optimizer evaluation

Compiler `public-query-v3` retains the reviewed content-scoping rewrite and adds
bounded inspection of the native JSON plan for its actual row-capped executable.
This is another EXPLAIN, never EXPLAIN ANALYZE or a discovery execution during prep.
It shares the existing transaction and deadline. Display-plan truncation does not
truncate this inspection. JSON inspection is limited to 1 MB, 4,096 nodes and 64
levels; unsupported evidence produces `content_scope_plan_unverified` rather than
claiming that physical work is bounded.

`content_scope` now reports that selected-document restrictions were *added*, not
that every scan is limited. `content_scope_shared_input` warns when a native
`__common_subplan_*` producer reads HTML primitives without the selected key CTE,
while its consumers apply that restriction later. The detector follows native CTE
indices through producer dependencies, excludes consumer subtrees, and does not
label inputs with their own content-key scan filters unrestricted. This narrowly
recognizes an observed barrier; absence of the warning does not prove low cost,
complete predicate propagation or file pruning. Diagnostics flow through ordinary
prep/execute responses and private preparation evidence.

Regression tests measure the shared producer and complete section-window row
counts, not just result equality. The diagnostic-only `common_subplan` alternative
is also compared across all 13 reviewed views at selective, empty and full domains,
including duplicate captures and column descriptions. The real read-only DuckLake
service test checks the warning in both preparation and execution.

`scripts/query_common_subplan_probe.py` provides the bounded read-only comparison
against configured reader credentials. It uses a disposable connection, alternates
variant order, pins each pair to one snapshot, validates installed definitions and
compares complete result multisets/types. It stops comparisons after a timeout or
result bound (100,000 rows / 32 MiB) rather than asserting partial equivalence.
It prints aggregate evidence only. Run it with `uv run python` from
`packages/periplus/`; `--case`, `--seconds` (default 20) and `--repetitions` (default 2)
limit the work.

No automatic optimizer selection is enabled by this evaluation. Production query
connections remain configuration-locked, with the normal native optimizer set.
Per-query toggling would require changing that security/lifecycle boundary;
globally disabling common-subplan extraction is not justified by selective-only
speedups. The production-data comparisons and remaining broad-query timeouts are
recorded in [the investigation](query-investigations/key-domain/README.md).

## Stable and experimental execution

The console defaults to **Stable** and offers **Experimental** beside Run. Shared
console links preserve the selected mode with `mode=experimental`. Stable uses
`/api/query/exec`, `/api/query/prep` and `/api/query/helpers`; experimental uses
`/api/query/experimental/exec`, `/api/query/experimental/prep` and
`/api/query/experimental/helpers`. The SQL assistant validates against the selected mode.

Stable and experimental share compiler `public-query-v11`, with mode suffixes
`:stable` and `:experimental`. Both include the promoted
`capture_heading_content_scope_v1` optimization.
Experimental additionally supports the execution-only selected-content rule below.
New candidates remain experimental until explicitly promoted.

`capture_heading_content_scope_v1` activates only for a conservative inner join of
`capture` and `html_heading` with a single exact `capture.effective_url` equality,
a plain content-ID join and string parameter bindings.
The original SQL binds first, and installed view definitions must match the reviewed
versions inside the pinned read transaction. Unsupported forms retain ordinary execution.
Native DuckDB optimizers remain enabled in both modes. They share public catalogue semantics
and lake layout. This split does not undo catalogue improvements or isolate
physical storage changes. QueryService owns the execution mode; future candidate
rewrites must be explicitly restricted to experimental until promoted.

Prepared and executed responses identify `query_mode`, `compiler_version` and
`optimizations` (empty when no rewrite applies). History records the mode in the compiler version,
including failed admission. The SQL console shows the public schema (`public_v1`)
and execution mode, not the internal compiler version; compiler metadata remains
available through the API and operator query history. Separate processes provide independent connection,
admission, memory and spill limits; they still share storage and cluster capacity.
Experimental unavailability is an error, never a retry through stable.

See [the first-three investigation](query-investigations/experimental-first-three/README.md)
for paired production evidence and rejected candidates. No shared physical layout
or catalogue relation changes are part of this activation.

### Promotion to the shared baseline

The existing experimental rules were promoted unchanged at the user's request.
The [promotion record](query-investigations/shared-query-baseline/README.md) links
the original frozen-snapshot evidence and records common-path activation tests.
Existing research-only candidates remain research-only. Stable is now the normal
optimized endpoint, while experimental remains available for future candidates.


### Experimental selected-content execution

`selected_content_scan_v1` resolves distinct non-null content IDs from a leading
filtered capture CTE during execution, then passes one bound array to exact
membership filters on HTML primitives. Selection and extraction share
the existing read transaction, admission slot and operation deadline. Prep binds
and explains the original statement and reports `selected_content_available`;
it never executes discovery. Execution reports the applied optimization and its
actual extraction plan while preserving the submitted SQL and parameters.

Initial grammar: one to four nonrecursive SELECT CTEs; the first directly projects
capture columns including content_id, has a WHERE and no joins, aggregates or
limit. Each subsequent SELECT block starts from that CTE. Heading, section,
and metadata joins must preserve its content domain through ordinary inner
or left joins, using content_id or a required equality to the driver content_id.
Auxiliary CTE joins use capture_id. Reviewed deterministic expressions and literal
or parameter UUID casts are supported; windows, correlated subqueries, arbitrary
functions, computed driver keys and unsupported joins remain native. This does
not cover an independent corpus-wide metadata aggregation CTE.

Installed view definitions must match within the transaction. Complete document
partitions are retained for section boundaries. Selection reads at most 100,001 capture-key rows to detect a 100,000-row
collection bound, then deduplicates in bounded application memory (at most 100,000
IDs and 8 MiB of UTF-8 key data). It does not add an unbounded DISTINCT aggregation
before the selection limit. Duplicate captures count toward the input-row bound. If the collection bound is exceeded, execution reports
`selected_content_bound` and uses the original SQL within the remaining deadline.
These are collection safety bounds, not a latency-based eligibility heuristic.

The acceptance priority is completion at bounded resources: modest slowdowns for
broad selections are accepted, but correctness and resource limits are unchanged.
[Investigation and evidence](query-investigations/request-scope/README.md) record
both gains and the broad synthetic regression. Stable execution is unchanged;
compiler v10 identifies this revision in both modes. No persistent tables, global
DuckDB optimizer settings, service limits or deployment topology are changed.


### Notebook input and streaming transport

`POST /query/exec` negotiates `application/x-ndjson` through Accept; existing JSON consumers
continue to use the buffered representation of the same operation. Both representations
use identical validation, preparation, snapshot, row/byte budgets and single-operation
admission. This is a delivery capability, not a catalogue change or optimizer rewrite.
No second execution, pagination, persistent cursor, service, queue or result storage is added.

The stream contains one `metadata` frame (prepared query, names/types, source snapshot and
effective limits), `rows` frames, and exactly one terminal `complete` or `error` frame.
Whitespace heartbeats are allowed. Completion includes row count, JSON row-array bytes,
elapsed time, truncation flag and the exhausted row/byte budget. EOF without completion
is a failed result. Completion is published only after transaction cleanup has succeeded.
Batches target 1,024 rows or 256 KiB; a single wider row may exceed the batch target but
must fit the total result budget. A two-frame queue provides backpressure to the same
worker thread that owns the DuckDB connection. Slow clients and disconnects cannot release
admission while that worker still operates. The gateway forwards Accept and streams frames
without buffering, caching or retrying them.

The SDK's synchronous `Client.stream()` and DB-API/SQLAlchemy engine consume this stream.
DB-API retains at most its current batch, keeps metadata separately, and rejects truncation
by default. `allow_partial=True` is explicit; broken streams always fail. `fetchall()` and
marimo still collect the final dataframe in notebook memory. SQLAlchemy reflection refuses
partial catalogue results even when normal queries opt into partial data.

`sql_api.bind(engine, content_ids=python_list)` exposes named bindings through another
marimo-discoverable SQLAlchemy engine sharing the original pool. SQL cells use
`content_id IN (SELECT unnest(CAST(:content_ids AS VARCHAR[])))`. The adapter binds scalar
lists as JSON data; it never interpolates ID literals or uploads a temporary table. Empty
lists work through the explicit cast. Separate notebook SQL cells still use independent
snapshots. The SDK README contains the complete Python filtering round trip.

Migration `20260911_0016` adds input budgets to the existing policy without changing existing
result budgets or rate windows. Roll out core, gateway and SDK together. Ingress must allow
the configured body size, streaming without response buffering, and 610-second transport
ceiling. Assistant and crawler clients may retain shorter caller deadlines.
