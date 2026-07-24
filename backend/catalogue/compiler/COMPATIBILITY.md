# Catalogue compiler compatibility

The intended system-wide chokepoint and remaining caller adoption work are
defined in [ADOPTION.md](ADOPTION.md). This document tracks SQL feature breadth;
adoption tracks whether every user-influenced SQL surface actually uses the
compiler.

Atlas users write ordinary DuckDB SQL. This matrix defines the SQL breadth Atlas intends to
optimize and the subset the compiler has proved today. It is a product contract and a test roadmap,
not a list of SQL hints for users. Optimization failure never makes valid SQL invalid: interactive
callers execute the original SQL and surface degraded performance. Materialization is stricter
because Atlas must prove its refresh plan; unsupported SQL remains queryable but is not eligible
for materialization.

## Product boundary

All in-process user-authored catalogue SQL passes through the embedded
`atlas_sql.AtlasCompiler` adapter. External callers use `atlas-sdk`; both return
the same typed result:
`invalid`, `unsupported`, `unchanged`, or `optimized`. Invalid SQL has no executable form.
Unsupported interactive SQL retains the authored executable SQL and a warning. Unsupported
materialization SQL is ineligible. Expected coverage gaps are data, not exceptions; compiler
defects still raise.

The debounced workbench compiler call uses this boundary without coverage logging. Interactive execution
and materialization creation use it with structured coverage telemetry containing purpose, outcome,
diagnostic codes, and a SQL fingerprint—but never authored SQL.

## Status vocabulary

| Status | Meaning |
|---|---|
| **Implemented** | Accepted by the compiler and covered by plan plus DuckDB semantic-equivalence tests. |
| **Implemented rejection** | Rejected with a stable, tested incompatibility code. |
| **Deferred** | Intended support; the compiler raises a structured optimization diagnostic. |
| **Incompatible** | The construct cannot safely use a particular optimization without changing semantics. |
| **Upstream blocked** | Safe support requires a documented DuckDB, DuckLake, DuckBasin, or Quack capability. |

## Keyed materialization

| SQL feature | Intended status | Implementation coverage | Notes |
|---|---|---|---|
| One direct driving-table scan | Implemented | Implemented | Qualified names and aliases are covered. |
| Scalar and composite stable keys | Implemented | Implemented | Comparisons are null-safe; bounded execution rows render as direct scan predicates. |
| Unchanged key projection | Implemented | Implemented | Direct renames preserve lineage; transformed or dropped keys fail closed. |
| Plain column projections | Implemented | Implemented | Direct columns and aliases retain exact lineage. |
| Unmodified safe wildcard projections | Implemented | Implemented | A single-relation `*` or explicitly qualified `relation.*` carries declared stable-key lineage through nested scopes. |
| Key-preserving wildcard modifiers | Implemented | Implemented | `EXCLUDE`, `REPLACE`, and `RENAME` are accepted when every named modification leaves all declared stable keys present, unchanged, and unshadowed. |
| Ambiguous or schema-dependent wildcard projections | Deferred | Implemented rejection | Multi-relation unqualified stars, pattern selection, key-touching modifiers, and explicit-CTE-column-list expansion require source schema metadata or violate stable-key lineage. |
| Boolean predicates without functions | Implemented | Implemented | Existing filters are preserved when key scope is injected. |
| Deterministic scalar expressions | Implemented | Implemented | Arithmetic, casts, `CASE`, and the tested deterministic function catalogue are covered. |
| Deterministic functions in predicates | Implemented | Implemented | Boolean composition and tested row-local functions are covered. |
| Unknown functions and unresolved macros | Deferred | Implemented rejection | Fail closed with `unsupported_function`. |
| Nondeterministic or clock-dependent functions | Incompatible | Implemented rejection | Random values, UUID generation, current time/session values, and sequence access fail with `nondeterministic_function`. |
| Inner equijoins and `USING` | Implemented | Implemented | Qualified `ON` equalities, renamed composite keys, and `USING` are covered. |
| Inner joins without key lineage | Deferred | Implemented rejection | Raise `unbounded_relation`; the view remains queryable but is not materialization-eligible. |
| Key-bounded left and right joins | Implemented | Implemented | The driving table remains on the preserved side; predicates are injected inside every bounded scan. |
| Driving table on nullable join side | Incompatible | Implemented rejection | A changed driving row cannot bound preserved-side unmatched output. |
| Key-bounded `FULL JOIN USING` | Implemented | Implemented | Every stable key participates in `USING`, every scan is bounded, and the scope projects DuckDB's merged unqualified key for matched and unmatched rows. |
| Root `FULL JOIN ON` with coalesced keys | Implemented | Implemented | One direct root full join may equijoin renamed key columns when each result key explicitly coalesces one qualified column from both preserved sides and lineage bounds both scans. |
| Other full outer joins | Deferred | Implemented rejection | Qualified one-sided keys, incomplete key joins, nested or chained `ON` forms, and non-coalesced expressions lack a safe alternative-origin key proof. |
| Non-recursive SELECT CTEs and derived tables | Implemented | Implemented | Direct projection and rename lineage propagates through nested scopes and joins. |
| Explicit CTE column lists | Implemented | Implemented | Positional output names propagate through nested scopes; malformed lists fail closed. |
| Scalar macros | Implemented | Implemented | Stored definitions expand recursively before deterministic-function validation. |
| Table macros | Implemented | Implemented | Positional/named arguments and defaults expand into derived scopes before lineage analysis. |
| Key-preserving positional `UNION ALL` | Implemented | Implemented | Every leaf independently preserves the key and proves bounded physical scans; key ordinals must align across branches. |
| Key-preserving `UNION ALL BY NAME` | Implemented | Implemented | Branch column order and non-key column presence may differ; every branch must expose the stable key by name. |
| Key-preserving set algebra | Implemented | Implemented | `UNION`, `INTERSECT`, and `EXCEPT`, with distinct or `ALL` semantics, operate independently within stable-key partitions. |
| Global set ordering | Implemented | Implemented | Membership-neutral `ORDER BY` is retained after branch compilation. |
| Set-root non-recursive CTEs | Implemented | Implemented | Each leaf receives only its transitive definition dependencies; unused definitions do not create false driving scans. |
| Global set `LIMIT` or `OFFSET` | Incompatible | Implemented rejection | Limits and offsets change membership outside the changed-key batch. |
| Exact-key aggregation | Implemented | Implemented | `COUNT`, `SUM`, `AVG`, `MIN`, and `MAX`; explicit grouping or `GROUP BY ALL` must resolve exactly to stable-key lineage. |
| Boolean, bitwise, and statistical aggregation | Implemented | Implemented | Boolean/bitwise reductions, conditional count, median, variance/deviation, correlation, and covariance recompute over complete stable-key groups. |
| Value-ordered collection and row-choice aggregates | Implemented | Implemented | `LIST`, `STRING_AGG`, `FIRST`, `LAST`, and `ANY_VALUE` require aggregate ordering that includes the exact emitted value; `ARG_MIN` and `ARG_MAX` require an ordering value that determines the emitted value. |
| Extra, transformed, or global grouping | Deferred | Implemented rejection | The declared result key would not identify exactly one complete recomputation group. |
| Unproven order-sensitive or row-choice aggregates | Deferred | Implemented rejection | Missing ordering, ordering independent of the emitted value, mode, and similar choices lack a total-order proof. |
| Plain key-local `DISTINCT` | Implemented | Implemented | The unchanged stable key partitions deduplication, so changed groups are independent. |
| Deterministic key-local `DISTINCT ON` | Implemented | Implemented | Partitions exactly by the stable key; ordering must determine every projected value, and wildcard output remains a schema-metadata fallback. |
| Stable-key-partitioned aggregate/ranking windows | Implemented | Implemented | Partitions include the full stable key; `QUALIFY`, ordering, frames, and extra dimensions are retained. |
| Deterministic key-local row-choice windows | Implemented | Implemented | `ROW_NUMBER`, `LAG`, `LEAD`, `FIRST_VALUE`, `LAST_VALUE`, `NTH_VALUE`, and positive-literal `NTILE` require one shared ordering that determines visible values and literal offsets. |
| Named window specifications | Implemented | Implemented | Local definitions and acyclic inheritance resolve to effective inline clauses before the same key-partition and determinism proofs run. |
| Global or cross-key windows | Incompatible | Implemented rejection | Their results depend on rows outside the changed-key batch. |
| Unproven row-choice windows | Deferred | Implemented rejection | Incomplete or mixed ordering, dynamic offsets or bucket counts, indirect projections, and other row choices lack the required proof. |
| Projection and row-local lateral `UNNEST` | Implemented | Implemented | Arguments are deterministic expressions correlated only to already bounded left-side rows. |
| Row-local lateral scalar projections | Implemented | Implemented | FROM-free deterministic lateral SELECTs may reference already available left aliases, compose in chains, and optionally filter each input to zero or one output row. |
| Arbitrary lateral subqueries and table functions | Deferred | Implemented rejection | Independent scans, aggregation, windows, row expansion, and unmanaged functions require separate ownership, cardinality, and side-effect proofs. |
| Recursive CTEs | Incompatible | Implemented rejection | No bounded incremental proof is planned initially. |
| Global or nested `LIMIT` or `OFFSET` | Incompatible | Implemented rejection | A change can alter membership outside the changed-key batch. |
| Ordering without a limit | Implemented | Implemented | Ordering is retained; bounded scan injection does not alter membership. |
| Dynamic SQL or external scans | Incompatible | Implemented rejection | `query*`, file/object readers, and external database scans fail with `unmanaged_relation`; diagnostic fragments redact arguments. |

## Interactive catalogue queries

Interactive compilation has no driving table, changed-key relation, or declared stable result key.
Valid SQL always remains executable through the original-query fallback. The statuses below refer
only to Atlas applying an automatic semantics-preserving optimization.

This is a separate compiler lane from keyed materialization. It may reuse purpose-neutral parsing,
resolution, lineage, determinism, and nullability analysis, but it must not inherit materialization's
stable-key or changed-row requirements. Conversely, an interactive optimization being implemented
does not imply that the same query shape is incrementally materializable.

### Planned delivery

| Phase | Planned support | Exit criterion |
|---|---|---|
| Foundation | Read-only parsing, macro resolution, reusable scope analysis, deterministic-function classification, and structured non-blocking fallback | Every valid query either returns semantics-equivalent SQL or a diagnostic that lets the caller execute the authored SQL unchanged. |
| Relational rewrites | Predicate propagation and pushdown, projection pruning, redundant scope/sort removal, set-branch filtering, aggregate/window input filtering, and safe limit/Top-K planning | Each rule has positive plan-shape tests, explicit safety-barrier tests, and DuckDB semantic-equivalence tests. |
| Reuse planning | Repeated deterministic expression/subquery elimination and CTE inline/materialize selection | Reuse choices preserve evaluation count where observable, respect authored CTE directives, and never cross volatile, correlated, or side-effecting boundaries. |
| Catalogue-aware planning | Managed-view expansion, bound-parameter selectivity, and DuckLake partition-aware predicates | All decisions use one coherent definition/physical-metadata snapshot and gracefully fall back when metadata is absent or stale. |
| Execution integration | Public query-path fallback, rewrite explanations, optimization outcome status, and scan/byte estimates | Interactive execution never blocks valid SQL because optimization is unavailable, and diagnostics explain outcomes without requiring users to author Atlas-specific SQL. |

### Resolution and analysis

| Capability | Intended status | Implementation coverage | Notes |
|---|---|---|---|
| Parse and normalize read-only DuckDB SQL | Implemented | Implemented | Produces ordinary DuckDB SQL and a reusable SQLGlot scope graph. |
| Preserve valid SQL outside materialization coverage | Implemented | Implemented | `SELECT *`, unknown ordinary functions, global queries, and other valid shapes are not subjected to keyed-materialization policy. |
| Resolve stored scalar macros | Implemented | Implemented | Definitions and signatures come from `duckdb_functions()` in DuckLake and expand recursively for analysis. |
| Resolve stored table macros | Implemented | Partial | Definitions and signatures come from DuckLake. DuckDB 1.5 does not expose default expressions, so calls relying on defaults degrade unchanged while explicit positional/named calls expand. |
| Unresolved `macros.*` definitions | Valid fallback | Implemented rejection | Raise `unsupported_function`; the caller executes the authored SQL against the installed catalogue macro. |
| Resolve managed views | Implemented | Implemented | `duckdb_views()` definitions expand recursively; cycles exceed the bounded expansion proof and degrade unchanged. |
| Shared scope, projection, and alias analysis | Implemented | Implemented | One immutable analyzed-query representation owns SQLGlot scopes, direct projection facts, and frozen cross-scope column lineage. Keyed policy only augments it for stable-key-specific wildcard and expansion proofs. |
| Shared join and nullability analysis | Implemented | Implemented | Purpose-neutral scope facts expose inner-join equivalence classes and outer-join nullability. Interactive propagation and keyed scan proofs consume those shared facts. |
| Function determinism and side-effect classification | Implemented | Implemented | Interactive rewrites combine the closed SQL taxonomy with DuckLake `has_side_effects` and stability metadata. Unknown functions, volatility, clock/session state, sequences, and explicit errors fail the relevant rewrite proof without invalidating SQL. |
| Bound-parameter awareness | Implemented | Implemented | Named values are supplied as immutable planning inputs, remain separate from authored/executable SQL, and enable partition-selectivity estimates only when a matching placeholder is proven. |
| Catalogue definition snapshot | Implemented | Implemented | Macros, views, and scalar-function safety metadata are read only from DuckLake, fingerprinted, cached for 60 seconds, and invalidated by catalogue DDL events. |
| Catalogue physical metadata snapshot | Implemented | Partial | The compiler owns one immutable revision containing definitions, stable table identities, partition transforms, and available statistics, and rejects mixed definition inputs. Production reads fence definitions, function safety, table UUIDs, row estimates, and file sizes between identical DuckLake snapshot IDs and retry concurrent DDL. Partition transforms and column statistics still await the lake-scoped Quack primitive recorded in `UPSTREAM.md`. |

### Automatic rewrites

| Optimization | Intended status | Implementation coverage | Safety boundary |
|---|---|---|---|
| Predicate pushdown through pass-through CTEs and derived tables | Implemented | Implemented | Direct projection aliases and proven deterministic row-local predicate functions are rewritten into single-use scopes; limits, grouping, windows, computed columns, shared scopes, and volatile/unknown functions are barriers. |
| Predicate propagation across inner equijoins and `USING` | Implemented | Implemented | Qualified column equalities form transitive equivalence classes; derived predicates retain the authored filter and compose with scope pushdown. |
| Predicate propagation across left joins | Implemented | Implemented | Preserved-side predicates constrain the nullable input through `ON`, a safe child scope, or a filtered physical wrapper; nullable-origin predicates never cross to the preserved side. |
| DuckLake partition-aware scan predicates | Implemented | Partial | Existing lineage rewrites render proven predicates at managed physical scans; snapshot partition metadata and bound parameters identify prunable predicates. Transformed-partition-specific rendering awaits production physical metadata. |
| Projection pruning in CTEs, derived tables, and expanded table macros | Implemented | Implemented | Unused direct-column outputs are removed to a fixed point; shared consumers are unioned and results, filters, joins, grouping, ordering, aggregates, and windows retain their dependencies. Wildcards, duplicate/explicit output names, positional clauses, distinctness, computed outputs, and volatile/unknown scopes are barriers. |
| Repeated scalar-macro expression elimination | Implemented | Implemented | Exact repeated deterministic scalar projections, including expanded macros, are evaluated once per row through collision-free lateral scalar projections for a totally ordered single ordinary-table query. Filters, joins, limits, partial ordering, aggregates/windows, volatile or unknown functions, and unmanaged scans are barriers. |
| Repeated derived-subquery elimination | Implemented | Implemented | Identical deterministic direct-column filtered scans used repeatedly by one totally ordered root query are hoisted into one collision-free generated `MATERIALIZED` CTE; relation aliases remain. Partial output ordering, limits, computed/volatile expressions, external scans, correlation, and differing definitions are barriers. |
| CTE inline/materialize choice | Implemented | Implemented | Preserve explicit user directives. Under a totally ordered root, deterministic direct-column filtered CTE scans over ordinary tables become `NOT MATERIALIZED` when single-use and `MATERIALIZED` when reused. Partial ordering, recursion, correlation, computed or volatile outputs, and unmanaged scans are barriers. |
| Filter pushdown into `UNION ALL` branches | Implemented | Implemented | Single-use positional unions rewrite direct projected columns in every branch atomically; the outer predicate remains. Named, deduplicating, globally modified, or computed-output unions are barriers. |
| Aggregate input filtering | Implemented | Implemented | Outer predicates composed from direct grouping-key outputs and proven deterministic row-local functions move into a single-use aggregate input while remaining outside; aliases and ordinals are resolved. Aggregate outputs, computed keys, grouping sets, rollups, and cubes are barriers. |
| `HAVING` simplification and pushdown | Implemented | Implemented | Standalone conjuncts composed from direct ordinary grouping keys and proven deterministic row-local functions are copied into the input `WHERE`; the complete authored `HAVING` remains. Aggregate predicates, mixed boolean expressions, computed keys, grouping sets, rollups, and cubes are barriers. |
| Partition-local window input filtering | Implemented | Implemented | Deterministic predicates on direct columns common to every inline window partition move into a single-use input while remaining outside. Global, partial-partition, incompatible multi-window, named-window, computed-partition, and shared scopes are barriers. |
| Safe `LIMIT` pushdown | Implemented | Implemented | A literal limit/offset on a single-use, row-preserving projection pushes `limit + offset` into an already totally ordered direct-column child while remaining outside. Unordered or partially ordered inputs, filters, joins, computed projections, sharing, parameters, and explicit CTE directives are barriers. |
| Top-K planning for `ORDER BY ... LIMIT` | Implemented | Implemented | A literal total-output order and limit/offset are rewritten through direct aliases into a single-use direct-column child as the same order plus `limit + offset`; all outer clauses remain. Partial orders, filters/joins, computed children, sharing, parameters, complex prior sorts, positional contracts, and explicit CTE directives are barriers. |
| Bounded scalar-macro evaluation | Implemented | Implemented | For a single ordinary source, the complete filter/order/limit/offset slice is selected in a generated `MATERIALIZED` CTE before recursively expanded scalar macros run. Macro arguments are bound to the outer source before expansion. Volatile, side-effecting, error-producing, unknown-function, joined, grouped, windowed, or computed-order shapes remain unchanged. |
| Redundant sort removal | Implemented | Implemented | A direct-column inner sort without a limit/offset is removed only when every consumer orders every unique direct output, making ties observationally identical. Partial ordering, wildcards, aggregates/windows, computed or volatile consumers, complex inner keys, and explicit CTE materialization directives are barriers. |
| Redundant pass-through scope elimination | Implemented | Implemented | Single-use derived tables containing only unique direct-column projections over one physical relation collapse recursively before other rewrites; outward names and join nullability are preserved. CTE/materialization boundaries, wildcards, `USING`, computed or duplicate outputs, clauses, directives, and positional contracts are barriers. |
| `UNNEST` and lateral predicate pushdown | Implemented | Implemented | Deterministic predicates depending exclusively on the qualified pre-expansion base row are copied into a derived input or a filtered physical wrapper for projection, cross, and lateral `UNNEST`; the authored filter remains. Expanded/mixed dependencies, subqueries, unknown/volatile functions, ambiguous columns, positional table aliases, and unmanaged scans are barriers. |
| Join reordering hints or rewrites | Deferred | Deferred | Prefer DuckDB's optimizer; intervene only with catalogue-specific evidence and a stable upstream contract. |
| External scan rewriting | Incompatible | Deferred | Atlas cannot assume ownership, snapshot, partitioning, or side-effect semantics for unmanaged relations. |

### Diagnostics and execution integration

| Capability | Intended status | Implementation coverage | Notes |
|---|---|---|---|
| Structured optimization-unavailable diagnostic | Implemented | Implemented | The compiler returns SQL or raises `QueryOptimizationUnavailable`. |
| Non-blocking performance diagnostics | Implemented | Implemented | Query status carries bounded advisory lint warnings plus structured compiler-fallback codes, messages, and documentation anchors. Diagnostics never block already validated SQL; applied rewrite proof remains a separate field. |
| Explain applied rewrites | Implemented | Implemented | An auxiliary interactive compilation result reports stable rule identifiers and bounded proof evidence only for rules that changed the query. The primary compiler contract remains SQL-or-raise, and execution status never exposes rewritten SQL. |
| Compare authored and compiled execution | Implemented | Implemented | Explicit `compiler.analyze()` calls run bounded JSON `EXPLAIN ANALYZE` profiles and report latency, CPU, rows scanned, bytes read, peak memory, cardinality, and comparative ratios. It never runs during lint debounce. |
| Estimate avoided scans or bytes without execution | Implemented | Partial | Compilation returns explicitly estimated rows and bytes avoided when coherent table size, partition, and distinct-count facts are supplied. Production estimates remain absent rather than guessed until Quack exposes those facts. |
| Public interactive execution integration | Implemented | Implemented | The shared API/agent boundary compiles after read-only catalogue validation, executes optimized SQL on success, and catches only `QueryOptimizationUnavailable` to execute the already validated authored SQL unchanged. Definition snapshots remain a separate catalogue-aware enhancement. |
| Query-status visibility of optimization outcome | Implemented | Implemented | The existing expiring query-state record stores `optimized`, `unchanged`, or `degraded_fallback` when the execution is registered; status consumers never need the internal rewritten SQL. |

Interactive compilation does not add arbitrary filters or limits, alter duplicate behavior, change
result ordering, replace exact operations with approximations, or guess user intent. Suggestions
that could change results belong in advisory diagnostics and are never applied automatically.

## Append and full materialization

| Strategy | Intended status | Implementation coverage | Notes |
|---|---|---|---|
| Append | Implemented | Implemented | Append refresh uses the same stable-key bounded compiler plan and additionally enforces insert-only CDC. |
| Full | Implemented | Implemented | Resolves authoritative macros and normalizes the broader read-only query without applying changed-key restrictions. |

## Compilation purposes

| Purpose | Implementation coverage | Fallback |
|---|---|---|
| Keyed materialization | Implemented | No executable fallback. Unsupported proof makes the definition ineligible. |
| Full materialization | Implemented | No executable fallback. Definition-resolution or generated-SQL failure makes the definition ineligible. |
| Interactive catalogue query | Implemented | Validate through the public read boundary, execute compiled SQL when available, and preserve the authored SQL as the non-blocking fallback. |
| Catalogue definition analysis | Implemented | Preserve the authored definition unchanged; report advisory diagnostics and direct or transitive catalogue dependency paths. |
| Graph-edge query | Implemented | Requires exactly one `$crawl_id`, an outer literal bounded `LIMIT`, a `url` output, and `edge.page_links`; valid unsupported optimization retains authored SQL only after those edge proofs pass. |

## Milestones

1. **Completed:** deterministic row-local expressions and predicates.
2. **Completed:** inner equijoins and `USING`.
3. **Completed:** non-recursive SELECT CTE and derived-table lineage.
4. **Completed:** key-bounded left joins with a preserved driving side.
5. **Completed:** scalar and table macro expansion.
6. **Completed:** remove authored scope directives, marker functions, and seeded scoped macros.

Each feature moves to **Implemented** only after plan-structure, negative-diagnostic, and local
DuckDB semantic-equivalence tests pass.
