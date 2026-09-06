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
one accidental optimizer plan. “Compiler” here means Periplus query analysis and the optional native
optimizer boundary; it does not restore the removed schema-dependent Python compiler.

## 1. Portable DuckLake catalogue

Periplus initialization installs and versions the complete public interface defined in
[`SCHEMA.md`](SCHEMA.md) as persistent DuckLake views over `ingest.*` and `material.*`. These
catalogue objects define the portable public semantics and remain correct without a Periplus SDK or
native extension. `periplus-setup` installs the complete catalogue or fails rather than publishing a
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

### Public application boundary

The public application's query client sends `POST /sql/query` with its server-only service
credential and validates the generic column, type, row, and truncation response.
Inspection uses accepted SQL statements; metadata routes are administrative.
The same service credential may submit a single-page crawl through `POST /crawls/`
and read narrow progress through `GET /crawls/{id}`. It cannot configure plans or policies,
list runs, retrieve graph snapshots, or perform maintenance.

The Next.js server owns product-specific queries, typed domain and page responses, application access policy,
rate limits, caching, and future authentication, billing and user state. Browsers call the Next.js API and never reach
the Periplus API directly. Periplus owns public-catalogue semantics and generic infrastructure
safety only: namespace validation, read-only enforcement, bounded results, and DuckDB/DuckLake
execution. It does not receive end-user identity or contain public-application business routes.

This boundary permits the server-only client to target the current Periplus API, an internal load
balancer, or a future read-only query deployment without changing product query definitions. All
implementations must preserve the same query contract and public catalogue semantics.

`periplus-public` owns the catalogue browser and SQL experience. Its crawl route accepts
one URL and fixes depth to zero, maximum pages to one, and the deadline to five minutes.
Core independently enforces these bounds for the public service credential. The public
server signs a seven-day receipt for the admitted run and requires that receipt before
reading progress. Acquisition completion does not imply catalogue visibility: ingestion
and materialization retain their independent lifecycle.

## 2. Python SDK

`packages/periplus-python-sdk/` is the first client package. It wraps DuckDB connection setup and
results and control-plane operations such as crawl submission and run
tracking. It does not define public catalogue semantics or compile and rewrite user SQL.
`periplus_sdk.conn.duck()` returns an ordinary `duckdb.DuckDBPyConnection` over the versioned public
catalogue using the same `PERIPLUS_DUCKLAKE_*` attachment contract as Periplus. Both runtime and SDK
connections select one connection protocol at their factory boundary; filesystem, S3, and
externally configured DuckDB URI storage do not create branches in query or materialization code.

## 3. Optional native optimization

Only a measured compiler/optimizer gap that remains after performance triage justifies native
code. Periplus ships one exact-version C++ DuckDB extension containing plan policy and specialist
internal functions. Base catalogue queries never require it, and extension functions do not
expand the public SQL contract.

Periplus will not maintain parallel stable-C and C++ production extensions or CI paths. Periplus images
compile and package the matching extension for hosted policy. SDK and direct clients may load a
matching host artifact, but do not require it before attaching DuckLake. The DuckLake catalogue
remains the authoritative interface. The local
build, direct DuckLake, and differential testing workflow is documented in
[`EXTENSION_DEVELOPMENT.md`](EXTENSION_DEVELOPMENT.md).

### Shared plan analysis and policy

The native extension has one Periplus plan-analysis boundary. It reduces DuckDB's optimized logical
plan to reusable facts about physical Periplus relations and grain, native capabilities, estimated
cardinality, expansion boundaries, blocking state, and the operator path connecting a hazard to
its consumer. An ordered policy registry consumes those facts and produces optimizer actions,
structured diagnostics, or enforced errors. Feature-specific code may contribute capabilities and
rules, but it must not install an independent whole-plan visitor.

The initial safety rules cover large Cartesian products and unbounded blocking state over Periplus
relations. The first optimizer rule removes `web.observation`'s visit/document join when no
document-derived value is used; selecting or filtering `content_id` retains it. Rewrites must be
optimizer actions from this shared policy rather than standalone SQL-spelling patches.

### Optimization development and regression loop

Checked-in real-user scenarios under [`benchmarks/query/`](../benchmarks/query/) are the unit of
performance work. Each case contains public SQL plus its user story, classification, scale ladder,
memory limit, ordering semantics, and completion budget. The shared runner executes the SQL
normally once and then through warm JSON `EXPLAIN ANALYZE`, recording the DuckLake snapshot, exact
result digest, scan work, peak buffer and temporary storage, and input/output cardinality around
every blocking operator.

Candidate and baseline reports may be compared only at the same DuckLake snapshot. Column names
and types, row count, bag multiplicity, values, and requested ordering must match exactly before a
performance result is accepted. The runner is benchmark orchestration only: it does not rewrite
SQL or participate in production query execution. Production benefits must remain in the public
catalogue and native extension.

Every optimization is tested on two growth axes: increasing the selected scope and increasing
unrelated background corpus data while holding that scope fixed. The second axis detects plans
whose cost still grows with the whole lake. Use `make query-benchmark ARGS="..."` for the complete
local protocol.

The internal native table function:

```sql
periplus_lint_query(sql, profile := 'interactive')
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
SET periplus_query_safety = 'enforce';      -- enforce, audit, or off
SET periplus_query_profile = 'interactive'; -- interactive or batch
```

Profiles are versioned cardinality and state budgets, not wall-clock predictions. Interactive
budgets protect request/console work; batch budgets permit deliberate larger work. Hard errors
require a high-confidence unsafe shape and an exceeded budget. `estimated_bytes` is a working-state
proxy derived from estimated rows and operator shape, not an exact peak-allocation prediction. The
Periplus shells continue to validate their public namespace boundary independently; exposing lint
through those shells or the SDK does not make this internal function part of the portable public
contract.
