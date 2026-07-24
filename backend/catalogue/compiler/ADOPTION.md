# Catalogue compiler adoption

This document defines the intended final compiler boundary and the work remaining
before Atlas can claim full compiler adoption. SQL feature coverage is tracked
separately in [COMPATIBILITY.md](COMPATIBILITY.md).

## Final goal

Every SQL statement whose meaning is authored or influenced by a user enters
Atlas through the embedded `atlas_sql.AtlasCompiler` adapter before it reaches
DuckDB or DuckLake. External Python callers enter through `atlas-sdk` and receive
the same versioned wire result.
The caller supplies an execution purpose and receives one typed decision:

- `invalid`: the SQL is not valid for the selected public boundary and must not
  execute;
- `unsupported`: the SQL is valid, but Atlas cannot prove an optimization;
- `unchanged`: Atlas proved the query is supported but found no useful rewrite;
- `optimized`: Atlas produced equivalent executable SQL and explains every
  applied rewrite.

For interactive purposes, `unsupported` always retains the valid authored SQL
as the executable fallback. Compiler coverage must never become a second SQL
validity gate. Materialization is deliberately stricter: Atlas may create or
refresh a materialization only when the selected refresh strategy has a
successful compiler proof.

Users write ordinary DuckDB SQL. They may create scalar macros, table macros,
and views without Atlas annotations. Atlas obtains definitions, dependencies,
function volatility, and side-effect metadata from DuckLake. Authored SQL must
never contain `atlas:scope`, `materialization_scope()`, a scoped macro variant,
or any other optimizer hint required only by Atlas.

`AtlasCompiler` is a product boundary, not a replacement for DuckDB's optimizer.
Atlas intervenes only where catalogue lineage, stable keys, CDC state, or
managed execution constraints allow it to prove something DuckDB cannot infer
from one isolated statement.

## What the chokepoint owns

The compiler owns:

- parsing and purpose selection;
- DuckLake macro and view resolution;
- dependency and lineage analysis;
- semantics-preserving rewrites;
- materialization eligibility and incremental scan scoping;
- structured diagnostics and applied-rewrite evidence;
- coverage telemetry using fingerprints rather than authored SQL;
- explicit comparative analysis through `compiler.analyze()`.

The compiler does not own:

- authorization;
- read-only or mutation policy;
- query timeout, cancellation, row, byte, or concurrency limits;
- DuckDB binding against the live catalogue;
- execution scheduling;
- trusted Atlas schema creation, ingestion, CDC, or housekeeping SQL.

Those controls remain at their existing boundaries. Compilation happens after
the caller has identified the statement as user-influenced and before execution.

## SQL classes

Every SQL call site must be classified into one of these classes:

| Class | Required path |
|---|---|
| Interactive user SQL | Validate the public read boundary, compile with the interactive purpose, then execute compiled SQL or valid authored fallback through the bounded runtime. |
| Stored user query | Compile for diagnostics when created or edited; compile again against the current DuckLake definition revision when executed. Saving valid unsupported SQL remains allowed. |
| User view or macro definition | Compile/analyze the definition with a definition purpose before DDL. Unsupported optimization does not invalidate otherwise valid DuckDB DDL, but materialization eligibility remains unavailable. |
| Materialization SQL | Compile with full, keyed, or append purpose. No unproved execution fallback is permitted for creation or refresh. |
| Crawl graph-edge SQL | Compile with a graph-edge purpose after edge-specific `$crawl_id`, output, and limit validation. Execution retains the pinned catalogue snapshot and edge resource bounds. |
| External SDK/notebook SQL | Use embedded or remote `AtlasCompiler`; remote callers receive the same result models and catalogue revision as Atlas callers. |
| Trusted Atlas infrastructure SQL | Use an explicitly named internal execution API. It bypasses user-query optimization by classification, not by accidentally calling DuckDB directly. |

## Current adoption

| Surface | Status | Current behavior |
|---|---|---|
| Workbench debounce/lint | Adopted | Calls `POST /catalogue/sql/compile`; invalid SQL is blocking, unsupported optimization is advisory, and rewrites appear as blue compiler activity. |
| Workbench execution | Adopted | Uses the shared validated interactive boundary and records optimization outcome, diagnostics, and rewrite evidence. |
| Analytics agents | Adopted | Agent SQL uses the same interactive compiler and bounded execution runtime as the workbench. |
| Materialization eligibility | Adopted | Full/keyed/append eligibility uses compiler purposes and fails closed when a refresh proof is unavailable. |
| Materialization execution | Adopted | Refresh SQL is compiled from the current DuckLake definition snapshot before execution. |
| Python SDK and HTTP compilation | Adopted | The independent `atlas-sdk` distribution provides typed sync and async remote clients; Atlas's embedded adapter returns the same wire models. |
| Comparative lake analysis | Adopted | Authored and compiled plans retain independent bounded outcomes, including partial results when only one plan succeeds. |
| DuckLake definitions | Adopted for compilation | Macro, view, and scalar-function safety metadata are read from DuckLake, fingerprinted, cached for 60 seconds, and invalidated by DDL events. |
| Saved-query create/update | Adopted | Every revision stores its advisory outcome, diagnostics, dependencies, compiler version, and definition revision. Execution still recompiles through the interactive boundary. |
| View definition create/update | Adopted | Definition analysis runs against the current DuckLake snapshot before DDL; managed references retain diagnostics and dependency paths. |
| Scalar/table macro create/update | Adopted | Definition analysis runs before DDL and retained control records expose diagnostics and direct or transitive dependency paths. |
| Crawl graph-edge validation/execution | Adopted | Definition validation and frozen runtime execution use `GraphEdgePurpose`; executable edge SQL and its catalogue-definition revision are frozen before admission, page-only edges retain standalone DuckDB execution, and historical edges retain their run-pinned DuckLake snapshot. |
| Administrative previews | Adopted | User-authored execution uses the interactive boundary; metadata-only reads use explicitly named trusted operations. |
| Internal SQL | Adopted | Repository and worker SQL uses explicitly named trusted connection, row, or execution APIs, enforced by architecture tests. |

## Missing work for full adoption

### 1. Completed: add the remaining compiler purposes

`CatalogueDefinitionPurpose` and `GraphEdgePurpose` are part of the public compiler
contract. Definition compilation must understand the object being created and
return dependency diagnostics without requiring optimization eligibility.
Graph-edge compilation must preserve the edge-specific `$crawl_id`, URL output,
literal limit, pinned-snapshot, and navigation-package rules.

### 2. Route definition management through the compiler — completed

View and macro create/update operations must request a compiler analysis before
issuing DuckLake DDL. Valid but unsupported definitions remain creatable.
Responses should expose current diagnostics and dependency paths. The installed
DuckLake object remains authoritative; callers must not provide separate
optimization metadata.

DuckDB 1.5 does not expose table-macro default expressions through
`duckdb_functions()`. Calls relying on defaults therefore remain a structured
fallback until DuckLake exposes the complete signature or the upstream metadata
contract changes.

### 3. Completed: adopt graph-edge SQL

Common parsing, macro/view resolution, diagnostics, and safe rewrites live in
`GraphEdgePurpose` while retaining edge validation and execution ownership in
the graph runtime. Historical edge queries must continue using their pinned
DuckLake snapshot. Page-only queries must continue using bounded standalone
DuckDB connections.

### 4. Compile saved queries at authoring time — completed

Saved queries should retain their authored SQL plus the latest diagnostics,
compiler version, and DuckLake definition revision for UI feedback. These
values are advisory snapshots, not executable caches. Execution must always
recompile against the current definition revision.

### 5. Classify every direct SQL execution — completed

Production calls to raw connection execution, remote rows, remote execution,
and definition helpers are classified as either:

- routed through a public compiler purpose because user input influences it; or
- executed through an explicitly named trusted-internal SQL boundary.

The pooled interactive executor accepts a `CompilationResult`, not arbitrary
SQL. The session-affine client exposes `trusted_connection`,
`trusted_remote_rows`, and `trusted_remote_execute`; the pooled runtime exposes
`trusted_remote_rows` for metadata-only operations. Architecture tests prevent
production imports of compiler internals, restoration of the unclassified
remote APIs, and raw catalogue execution outside its owning boundaries.

### 6. Complete observability

Central metrics must report purpose, outcome, diagnostic codes, rewrite rules,
compiler version, definition revision, and compilation latency. They must not
record authored SQL or literals. Coverage views should distinguish unsupported
syntax, unavailable metadata, unsafe semantics, compiler defects, and upstream
limitations.

**Implemented:** compilation decisions now emit privacy-safe Prometheus
counters/histograms and structured coverage events. Definition revisions and
fingerprints remain in structured events rather than unbounded Prometheus label
sets. Unexpected defects have a dedicated counter and never include authored SQL.

### 7. Harden analysis and failure behavior

`compiler.analyze()` should return independent authored and compiled outcomes.
An authored query timing out or exhausting memory while the compiled query
succeeds is a useful result, not a failed comparison. Add cancellation,
per-plan budgets, operator-level differences, optional equivalence hashes, and
clear cold/warm execution policy.

**Implemented:** authored and compiled plans return independent outcomes under
per-plan budgets; cancellation propagates to the bounded runtime; timeout,
memory, execution, and internal failures are classified; successful dual
profiles include operator differences; cold/warm policy and optional bounded
result equivalence hashes are explicit API and SDK inputs.

### 8. Enforce the boundary

Full adoption needs architectural tests, not convention alone:

- production user-facing modules may import `AtlasCompiler`, but not
  `compile_catalogue_sql` or compiler internals;
- user-facing executors accept a `CompilationResult` or compiler purpose rather
  than arbitrary executable SQL;
- low-level internal SQL APIs require an explicit trusted classification;
- every public purpose has fallback, negative-proof, and DuckDB equivalence
  tests;
- compiler unavailability cannot silently claim that SQL is valid or optimized.

## Completion criteria

Atlas can call compiler adoption complete when:

1. every user-influenced SQL surface in the table above is adopted;
2. the direct-execution audit has no unclassified production call sites;
3. DuckLake is the only compiler source for macro and view definitions;
4. interactive unsupported SQL always remains executable after public
   validation;
5. materialization never executes an unproved refresh plan;
6. graph-edge semantics and pinned snapshots are preserved through their
   compiler purpose;
7. coverage telemetry centrally explains every compiler outcome;
8. architecture tests prevent a new bypass from being introduced.
