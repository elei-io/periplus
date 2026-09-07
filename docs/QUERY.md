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

The public app separates the marketing landing page (`/`), discovery workspace (`/discover`,
with Ask and SQL modes), live coverage (`/coverage`), curated dataset queries (`/datasets`), and one-time page requests (`/suggest`). Curated datasets are named SQL definitions with descriptions and scope notes; their previews execute through the same query API without storing separate result copies. Home submissions navigate to
the workspace and execute once; ordinary shared SQL links restore a draft without executing. Next.js owns web-specific agent orchestration using Vercel AI SDK,
with a read-only SQL tool that calls the Python query API. The assistant cannot crawl or access
lake credentials. The SQL bench offers editable examples, schema queries, export, and share links.
Coverage suggestions use the control API; crawl completion is not proof of indexing readiness.
SQL preparation and execution remain in the separate Python
`periplus-query` process, which exposes only `POST /query/prep`, `POST /query/exec`, and
`GET /query/helpers`, and its health probe. Query routes do not exist on the control API.

Both operations accept SQL and positional parameters and use the same public SQL validation.
Preparation binds and explains without executing the analytical query, returning SQL, parameters,
a query ID, diagnostics, and a plan. SQL currently remains unchanged; DuckDB optimizes its plan.
Execution always prepares independently, then runs the query. It does not trust a prior prep call.
The browser renders server results; there is no Wasm runtime, metadata export, file proxy, or
client telemetry endpoint. Server logs capture SQL, parameters, outcome, and elapsed time.

Each query process owns one read-only DuckLake connection and accepts one operation at a time.
Excess requests return 429 with Retry-After; there is no queue. A 20-second interrupt deadline,
512 MiB DuckDB memory budget, 256 MiB spill budget, and 1,000-row / 8 MiB result budget bound
execution. Result truncation is explicit. Integers outside JavaScript's safe range and decimals
are returned as strings; SQL column types accompany results. Disconnecting does not promise
cancellation: the server deadline still bounds the work.

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
results and control-plane operations such as crawl submission and run
tracking. It does not define public catalogue semantics or compile and rewrite user SQL.
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
