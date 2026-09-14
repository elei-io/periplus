# Query

The canonical element replacement is specified in [ELEMENT_LAYOUT.md](ELEMENT_LAYOUT.md).

Exact page-word lookup uses `search(terms)` in both `public_v1` and `experimental`.
The term/content postings remain private in `material.html_terms`. Search accepts
up to 32 case-folded, NFC-normalized word keys and returns matching content IDs,
containing element indexes and distinct-word coverage scores. Join `capture` for
page identities and observation times. See [SCHEMA.md](SCHEMA.md) for exact semantics.

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

Stable defaults to `public_v1`; Experimental defaults to `experimental`.
An omitted or null `schema_version` selects the endpoint namespace; an explicit
value must match it. Each connection selects its namespace before locking configuration. Responses report the
resolved version; saved queries should preserve it separately from `source_snapshot`.
Stable has one released versioned namespace. Experimental has independent definitions and no cross-schema aliases.

### Query server boundary

The public app provides a landing page, SQL console and shared website coverage. The SQL console supports read-only queries and CSV/JSON export. Its optional Ask SQL assistant can execute exploratory SQL and prepares editor proposals for explicit user review. Coverage requests use the control API; crawl completion is not proof of indexing readiness.

SQL preparation and execution remain in the separate Python
`periplus-query` process, which exposes only `POST /query/prep`, `POST /query/exec`, and
`GET /query/helpers`, and its health probe. Query routes do not exist on the control API.

Both operations accept SQL and positional parameters and use the same public SQL validation.
Preparation binds and explains without executing the analytical query, returning SQL, parameters,
a query ID, diagnostics, and a plan. Compiler `public-query-v13` applies the promoted
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

Choose captures, then query `html_element.text` and `text_direct` using ordinary
SQL. Full descendant text is materialized. `search(terms)` returns
`content_id`, sorted distinct `node_indexes`, and a DOUBLE score counting distinct
requested word keys found per content. Pass up to 32 existing ICU case-folded,
NFC-normalized keys, for example `search(['robot', 'science'])`. It matches
any requested word; node indexes combine the containing elements. Duplicate query
keys do not increase score. This is not a phrase parser or BM25 ranking.

Use `ORDER BY score DESC, content_id LIMIT 20` for deterministic top results.
Empty/null input lists return no rows; query result and execution limits still apply.

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

The helper registry declares `search(terms)` and derives its signature, score
semantics, examples and input limit from the same catalogue object.

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

Historical experiments and dated validation logs are in
[QUERY_INVESTIGATION_HISTORY.md](QUERY_INVESTIGATION_HISTORY.md). They are evidence,
not current API instructions.

## Stable and experimental execution

The console defaults to **Stable** and offers **Experimental** beside Run. Shared
console links preserve the selected mode with `mode=experimental`. Stable uses
`/api/query/exec`, `/api/query/prep` and `/api/query/helpers`; experimental uses
`/api/query/experimental/exec`, `/api/query/experimental/prep` and
`/api/query/experimental/helpers`. The SQL assistant validates against the selected mode.

Stable serves only `public_v1.*`; Experimental serves only `experimental.*`.
Qualified cross-schema reads and inspection are rejected. Unqualified `search(['robot'])`
resolves in the endpoint's namespace. Requests cannot change endpoints through `schema_version`.
Helper discovery includes `schema_version` and registry-derived `relations`; the explorer,
editor completion and SQL assistant use that endpoint's definitions.

Experimental declarations live in `experimental_registry.py`, `experimental_helpers.py`
and `sql/experimental/`. They start with the same view shapes as public_v1 but are
independent SQL over the existing physical evidence, not aliases of stable views.
Change these declarations to experiment without editing `public_registry.py` or
`sql/public_v1/`. Install both catalogues transactionally through the existing installer;
ordinary processes do not create or repair schemas. View-only changes need no corpus rebuild.

Promotion is a reviewed release: copy the selected experimental contract into a new
`sql/public_vN/` and its versioned manifest, update the stable schema selection and client
contract, then install and deploy together. Do not point a released version at mutable
experimental views. Physical projection changes still follow the full rebuild lifecycle.

Stable and experimental share compiler `public-query-v16`, with mode suffixes.
Stable runs ordinary validated SQL. Experimental can apply
`element_text_index_candidates` to literal exact-text predicates on one direct
`html_element` relation. A complete interior ASCII word surrounded by spaces is a
safe candidate anchor even when surrounding HTML text joins an element's edge
words. Single words, unsupported shapes, bound text parameters, collations,
joins/CTEs and known exact content selections remain unchanged. The original
text equality always remains; this never substitutes word search for substring
or exact-string semantics.

Execution first binds the original public SQL, checks the installed view definitions,
and reads at most 129 term/content posting rows in the request's snapshot and deadline.
More than 128 contents, 1,024 nodes per content or 16,384 candidate nodes declines
without truncating matches. A skinny private scan resolves matching element identities
and exact code-point lengths to native DuckLake row IDs. The final scan uses those IDs
plus their minimum/maximum range and still evaluates the original exact text predicate.
Only the canonical public element columns are exposed. Row IDs never leave the request
or become a public identity, cache or stored index; all stages share one snapshot.

Preparation performs no index lookup and reports eligible execution work with
`element_text_index_candidates.deferred.candidate_lookup`. All attempted passes
report typed decision status and reason through safe diagnostics; candidate limits
and contract mismatches are distinguishable from unsupported query shapes. Execution reports the selected plan and optimization
while preserving submitted SQL and parameters. Public namespace validation still
runs before compilation; only generated SQL can read the private physical relation.
Processes retain independent admission and resource limits. Experimental failure
is never retried through stable. This narrow candidate has no speed guarantee for
dispersed row IDs or common words, and is not promoted to stable.

Prepared/executed responses and private history retain mode, compiler version,
SQL, parameters and plan evidence. Native DuckDB optimizers remain enabled.

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


The former capture/heading-specific compiler pass was removed with html_heading.
Use html_element tag predicates for heading analysis. The catalogue simplification
is a schema-contract change, not an optimizer performance promotion.

## Query development

The [query developer guide](../packages/periplus/src/periplus/query/README.md) maps
the implementation and gives the add/remove/promote workflow. `models.py` owns
API types; `compiler.py` owns binding, ordered pass alternatives and diagnostics;
`service.py` owns admission, snapshots, deadlines, result delivery and cleanup.
The explicit registry has no stable passes and one experimental pass. First applied
pass wins; there is no implicit rewrite chaining or dynamic discovery.
