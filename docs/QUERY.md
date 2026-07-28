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

Atlas initialization installs and versions the `web.*` and `dom.*` interface as persistent
DuckLake views,
scalar macros, and table macros over `ingest.*` and `material.*`. These catalogue objects define
the complete public semantics and must remain correct without an Atlas SDK or native extension.
They are changed only by Atlas catalogue upgrades, never by Quack replica startup.

The authoritative one-object SQL definitions live under
`backend/src/atlas/platform/catalogue/sql/web/` and
`backend/src/atlas/platform/catalogue/sql/dom/`, split into `macros_scalar`, `views`, and
`macros_table`. The explicit manifest in `atlas.platform.catalogue.public` installs them in
dependency order. `atlas-setup` replaces the complete interface transactionally after reconciling
the typed physical schema, then validates object names, columns, macro kinds, view comments,
manifest descriptions, and catalogue version. Ordinary processes validate this contract and
never repair it at startup.

`dom.documents` exposes per-content node counts and depth for costing. The keyed
`dom.document(content_id)` table macro returns one canonical nested DOM and is the bounded input
to optional native selector functions. Page-first selector plans reduce and deduplicate content
identities before invoking that macro.

Every public view and view column has a concise description derived from the semantic contract in
`SCHEMA.md`. Setup reapplies supported view comments after replacing each view. DuckLake does not
currently accept `COMMENT ON COLUMN` for view columns, so the authoritative manifest supplies
column descriptions to the SQL metadata endpoint and both shells.

Recurring expensive computations belong in Atlas-owned `material.*` relations maintained through
the ordinary materialization lifecycle. Quack is an optional execution transport, not a dependency
of the catalogue contract.

The shared terminal and web shell accepts bounded read-only SQL over qualified `web.*` and
`dom.*` relations only. It also accepts `DESCRIBE`, `EXPLAIN`, `EXPLAIN ANALYZE`, and `SUMMARIZE`
when their target passes the same public-namespace validation, plus `SHOW TABLES FROM web` and
`SHOW TABLES FROM dom`. Its metadata and autocomplete endpoints expose public views plus scalar
and table macro signatures, including `dom.get_attribute`, `dom.text_content`,
`web.page_history`, and `web.link_history`, but no `ingest.*` or `material.*` objects. The shared
shell uses that metadata for `.tables`, `.macros`,
`.describe`, and context-aware completion; `.completion reload` refreshes it explicitly.

`.ai <question>` adds a request-scoped assistant over the same public query boundary. The server
may inspect public metadata and run row-bounded, read-only queries; it cannot access physical or
control-plane schemas. SQL drafts are accepted only after Atlas validates the statement and binds
it with `EXPLAIN`. The shell keeps a small recent conversation context locally, while the server
stores no assistant session state. During a turn, each SQL operation exposes its purpose, elapsed
time, row count, and final status without dumping the full query into the transcript. A completed
turn renders a compact conclusion, evidence, and optional recommendation followed by a transient
review panel. In the panel, `W` toggles between validated drafts and the exact SQL work ledger,
Tab and Shift-Tab cycle entries, Up and Down page through formatted SQL, `C` copies it, and Escape
dismisses it. Enter loads a selected draft into the normal editable prompt without executing it.
`.ai --fresh <question>` begins without prior assistant context.

## 2. Python SDK

`packages/atlas-python-sdk/` is the first client package. It wraps DuckDB connection setup and
results, Atlas authentication, and control-plane operations such as crawl submission and run
tracking. It does not define public catalogue semantics or compile and rewrite user SQL.

## 3. Optional native optimization

Only a measured compiler/optimizer gap that remains after performance triage justifies native
code. If required, Atlas will ship one exact-version C++ DuckDB extension containing SQL rewrite
rules and specialist functions. The extension may make portable public queries faster but must
never be required for correctness.

Atlas will not maintain parallel stable-C and C++ production extensions or CI paths. With Quack,
the matching extension is loaded on the server and the complete query executes there. Without
Quack, a user may load the matching extension locally. In both cases the DuckLake catalogue remains
the authoritative interface. The local build, direct DuckLake, and differential testing workflow is
documented in [`EXTENSION_DEVELOPMENT.md`](EXTENSION_DEVELOPMENT.md).

### Shared plan analysis and policy

The native extension has one Atlas plan-analysis boundary. It reduces DuckDB's optimized logical
plan to reusable facts about physical Atlas relations and grain, native capabilities, estimated
cardinality, expansion boundaries, blocking state, and the operator path connecting a hazard to
its consumer. An ordered policy registry consumes those facts and produces optimizer actions,
structured diagnostics, or enforced errors. Feature-specific code may contribute capabilities and
rules, but it must not install an independent whole-plan visitor.

The initial rules cover both DOM and non-DOM plans: unsafe collection of element-grain rows before
document evaluation, large Cartesian products between Atlas relations, unexpectedly broad
document operations, repeated DOM work, immediate selector-list expansion, and unbounded blocking
state over large Atlas relations. The selector dynamic-filter adjustment is an optimizer action
from this shared policy rather than a standalone selector patch.

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
