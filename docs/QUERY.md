# Query

Periplus delivers its query interface in three layers, in this order.

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
`packages/periplus/src/periplus/platform/catalogue/sql/web/` and
`packages/periplus/src/periplus/platform/catalogue/sql/content/`. The explicit manifest in
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

The shared terminal and web shell accepts bounded read-only SQL over the qualified relations in
the public registry only. It also accepts `DESCRIBE`, `EXPLAIN`, `EXPLAIN ANALYZE`, and `SUMMARIZE`
when their target passes the same public-namespace validation, plus `SHOW TABLES` for each public
schema. The shared shell derives `.tables`, `.describe`, and context-aware completion through that
same query operation; `.completion reload` refreshes the derived information explicitly.

### Query server boundary

The public app separates the marketing landing page (`/`), dataset discovery conversation (`/discover`), a separate SQL workspace (`/sql`), live coverage (`/coverage`), curated dataset queries (`/datasets`), and one-time page requests (`/suggest`). Curated datasets are named SQL definitions with descriptions and scope notes; their previews execute through the same query API without storing separate result copies. Home submissions navigate to
the workspace and execute once; ordinary shared SQL links restore a draft without executing. Next.js owns web-specific agent orchestration using Vercel AI SDK,
with a read-only SQL tool that calls the Python query API. The assistant cannot crawl or access
lake credentials. The SQL bench offers editable examples, schema queries, export, and share links.
Coverage suggestions use the control API; crawl completion is not proof of indexing readiness.
The dataset builder turns a user idea into an editable specification with ordered typed columns,
row grain, population, observation selection, and acceptance rules. Before choosing source-specific
semantics, the agent inventories the available catalogue and inspects representative page structures
and links. Its source plan cites successful queries from that request and explains how the material
supplies the requested output. Listing cards can supply multiple records and outgoing detail URLs;
a detail URL does not require a collected detail page. Only unresolved choices affecting population,
meaning, or missing-value policy should interrupt the build. Source evidence is inspectable and
exported alongside the definition. A build identifies its executed
dataset query separately from coverage evidence and attaches executed validation queries. The server
checks exact output names/types, required values, nonempty complete results, resolved design choices,
and passing evidence-backed checks before accepting a ready result. The model receives at most 20
sampled rows while the UI retains the bounded query result (up to 1,000 rows). Users can export CSV
and download the definition, dataset SQL, check SQL, and source snapshot reference. The reference
records provenance; it does not pin future executions or guarantee extraction after layout changes.
The definition appears in a persistent side panel while the conversation holds source findings,
questions and result previews. Generated SQL can open in the separate SQL workspace in a new tab;
shared query links target `/sql` and restore drafts without executing. The workflow remains temporary
browser state and adds no storage or execution service.
SQL preparation and execution remain in the separate Python
`periplus-query` process, which exposes only `POST /query/prep`, `POST /query/exec`, and
`GET /query/helpers`, and its health probe. Query routes do not exist on the control API.

Both operations accept SQL and positional parameters and use the same public SQL validation.
Preparation binds and explains without executing the analytical query, returning SQL, parameters,
a query ID, diagnostics, and a plan. SQL currently remains unchanged; DuckDB optimizes its plan.
Execution always prepares independently, then runs the query. It does not trust a prior prep call.
Execution returns `source_snapshot`, obtained from `ducklake_current_snapshot` inside the same
read transaction before binding or executing the query. The result and snapshot therefore describe
one consistent source even if another writer commits concurrently.
The browser renders server results; there is no Wasm runtime, metadata export, file proxy, or
client telemetry endpoint. Server logs capture generated operation IDs, safe outcomes, truncation, row counts and elapsed time. SQL text, parameters, plans and result rows are not logged.

Each query process owns one read-only DuckLake connection and accepts one operation at a time.
Excess requests return 429 with Retry-After; there is no queue. A 20-second interrupt deadline,
default 512 MB DuckDB memory budget, default 256 MB spill budget, and 1,000-row / 8 MiB result budget bound
execution. Deployment-owned `PERIPLUS_DUCKDB_THREADS`, `PERIPLUS_DUCKDB_MEMORY_LIMIT`
and `PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE` override connection sizing before
configuration is locked; Helm exposes them under `query.duckdb`. Each replica
still admits one operation. These capacity settings do not change SQL semantics,
credentials, deadlines, or result limits. Result truncation is explicit. Integers outside JavaScript's safe range and decimals
are returned as strings; SQL column types accompany results. Disconnecting does not promise
cancellation: the server deadline still bounds the work.
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

## 2. Python SDK

`packages/periplus-python-sdk/` is the first client package. It wraps DuckDB connection setup and
results and control-plane operations for collection submission, current/history status, and
versioned crawler/domain controls. Collection settlement and structural query readiness are separate. It does not define public catalogue semantics or compile and rewrite user SQL.
`periplus_sdk.conn.duck()` returns an ordinary `duckdb.DuckDBPyConnection` over the versioned public
catalogue using the same `PERIPLUS_DUCKLAKE_*` attachment contract as Periplus. Both runtime and SDK
connections select one connection protocol at their factory boundary; filesystem, S3, and
externally configured DuckDB URI storage do not create branches in query or materialization code.

## 3. Query API optimization

The query API owns validation, diagnostics, and future semantics-preserving SQL optimizations.
Periplus uses standard DuckDB and does not ship a custom query extension. The native DuckLake CDC
extension belongs only to live materialization and does not participate in query preparation.

Before adding an optimization, follow the performance triage above and record representative
plans and scale. Verify equivalent column types, values, multiplicities, and required ordering
at the same snapshot. The existing benchmark runner and query cases remain useful for this work.

## SQL helper development

The explicit helper registry is `packages/periplus/src/periplus/platform/catalogue/helpers/`.
Its README documents the add/edit/test/install workflow. Each declaration provides a SQL
resource, signature, output descriptions, limits, and executable examples. The public manifest
installs the macros alongside views; `GET /query/helpers` derives documentation from that same
manifest. Next.js proxies discovery and loads it into the agent context for each request.
There is no helper-specific Python execution or prep-time rewrite path.

The initial `content.subtree_text` helper addresses a catalogue usability/correctness gap:
callers were reconstructing DOM text incorrectly. It is not an optimizer workaround. The
implementation orders direct-text and tail events by document position, placing nested tails
before ancestor tails and excluding the root tail. Selected element count and output characters
are bounded; ordinary query limits still govern physical scan cost. Contract tests compare every
subtree with parser text, exercise lateral calls, and install the helper into read-only DuckLake
query-service fixtures. Future performance changes follow the triage above.

### Query failure categories

Query failures return a safe `code` alongside `detail`: `sql_invalid` (422), `helper_limit`
(422), `resource_limit` (408 for time or 422 for memory), `service_busy` (429),
`storage_unavailable` (503), or `query_failed` (500). Native DuckDB errors never expose
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
mean imported evidence or ingestion lag and is not a proof that no other relationships exist.

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
