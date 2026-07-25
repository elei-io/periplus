# Catalogue compiler

Atlas users write ordinary DuckDB SQL. The catalogue compiler applies purpose-aware,
semantics-preserving performance rewrites using knowledge that is unavailable to DuckDB alone:
managed table identities, stable keys, macro definitions, DuckLake partitioning, and Quack
execution boundaries.

The compiler exists to make the evidence-to-analysis path safe and incremental, not to introduce a
new query language. The cross-page and cross-domain workloads used to judge that path are defined
in [Analytical benchmarks](ANALYTICAL_BENCHMARKS.md).

The executable support contract and implementation status live beside the compiler in
[`COMPATIBILITY.md`](../backend/catalogue/compiler/COMPATIBILITY.md).
The final system-wide chokepoint, current caller inventory, and remaining
adoption work are defined in
[`ADOPTION.md`](../backend/catalogue/compiler/ADOPTION.md).

## Public SDK and HTTP contract

Atlas execution, analytics, linting, and materialization use the internal embedded
`atlas_sql.AtlasCompiler` adapter. External callers use the independently
packaged `atlas-sdk`, which contains only the typed HTTP contract and has no
DuckDB, SQLGlot, worker, storage, or control-plane dependency:

```python
from atlas_sdk import AtlasClient

with AtlasClient(
    "https://atlas.example.com",
    token=atlas_token,
) as atlas:
    result = atlas.compiler.compile(
        """
        SELECT element_index,
               macros.readable_text(document_id, element_index)
        FROM elements
        WHERE document_id =
              '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
        ORDER BY element_index
        LIMIT 100
        """
    )

if result.valid:
    rows = connection.sql(result.executable_sql).fetchall()
```

The SDK calls `POST /catalogue/sql/compile`; it never executes SQL. Embedded and
remote results include the authored and executable SQL, outcome, diagnostics,
applied rewrites, compiler version, protocol version, and catalogue-definition
revision. Transport, authentication, permission, request, service, and protocol
failures use distinct SDK exceptions and never claim that unvalidated SQL is
valid.

Lake-backed comparison is explicit because it executes both plans:

```python
analysis = atlas.compiler.analyze("""
    SELECT element_index,
           macros.readable_text(document_id, element_index)
    FROM elements
    WHERE document_id =
          '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
    ORDER BY element_index
    LIMIT 100
""")

print(analysis.comparison.latency_speedup)
print(analysis.comparison.rows_scanned_reduction)
```

The SDK calls `POST /catalogue/sql/analyze`. Atlas runs
`EXPLAIN (ANALYZE, FORMAT JSON)` through the same bounded interactive query
runtime used for catalogue reads and returns latency, CPU, bytes read, peak
buffer memory, rows scanned, cardinality, and comparative ratios. Analysis is
never part of debounced linting. If compilation leaves SQL unchanged, Atlas
profiles it once rather than running the identical plan twice.

Analysis applies an explicit per-plan budget and records each plan independently
as `succeeded`, `timed_out`, `cancelled`, `memory_exhausted`, or `failed`.
A failure of one plan therefore does not discard the other plan's profile.
Comparative ratios and operator differences are present only when both profiles
succeed. Callers may select a cold single-profile policy or a warm policy with
bounded warm-up runs. Optional equivalence hashes execute both bounded result
streams and hash their column, type, and ordered-row representation; they are
never collected implicitly. Client cancellation propagates through the existing
interactive runtime and interrupts the active DuckDB operation.
“Cold” means Atlas performs no intentional warm-up; it does not claim to flush
DuckBasin, DuckLake, operating-system, or object-store caches. “Warm” performs
the requested bounded warm-up profiles in the same session before retaining the
measured profile.

Every compiler decision emits low-cardinality Prometheus counters and latency
histograms for purpose, outcome, diagnostic category, stable diagnostic code,
rewrite rule, compiler version, and caller class. A structured coverage event
also carries the catalogue revision and a truncated SHA-256 fingerprint.
Authored SQL and literals are never metric labels or log fields. Diagnostic
categories distinguish unsupported syntax, unavailable metadata, unsafe
semantics, upstream limitations, and unexpected compiler defects.

## Query boundary

`catalogue.compiler.compile_catalogue_sql(...)` is the product chokepoint. It returns a typed
`SqlCompilationResult` whose outcome is `invalid`, `unsupported`, `unchanged`, or `optimized`.
Interactive callers execute the authored SQL for valid unsupported queries and surface the
structured degraded-performance diagnostic. Materialization callers require a supported result,
because an unproved refresh plan cannot safely maintain the view.

When no rewrite or catalogue-definition expansion is applied, interactive
compilation returns the authored SQL byte-for-byte. Every generated statement is
parsed by DuckDB before it can be returned. A generated statement that DuckDB
cannot parse becomes an interactive authored-SQL fallback and makes full or
incremental materialization ineligible.

`GraphEdgePurpose` validates the navigation contract before any optimization
fallback: exactly one `$crawl_id`, an outer literal bounded `LIMIT`, a `url`
output, and a read of `edge.page_links`. Page-only edges execute through bounded
standalone DuckDB. Historical edges compile with authoritative catalogue
definitions and execute against the graph run's pinned DuckLake snapshot. A
graph run freezes each edge's executable SQL, catalogue dependency, compiler
definition revision, and snapshot requirement before admitting its first URL;
redelivery never recompiles an edge against newer definitions.

The lower-level proof functions retain their SQL-or-`QueryOptimizationUnavailable` contract. Only
the chokepoint translates that expected coverage exception into result data; unexpected compiler
defects continue to raise.

Validation and security remain separate boundaries and may reject invalid or prohibited SQL. The
optimizer only answers whether Atlas can prove an equivalent, more selective execution shape.

`QueryOptimizationUnavailable.as_dict()` exposes a stable reason code, message, relevant SQL
fragment, and documentation anchor. Consumers do not classify SQL themselves.

## Shared analysis and purpose policy

Purpose-neutral analysis parses the authored query, resolves authoritative scalar and table macro
definitions, normalizes ordinary DuckDB SQL, and builds one SQLGlot scope graph. Purpose policies
consume that immutable result rather than parsing or resolving catalogue definitions independently.

`KeyedMaterializationPurpose` applies stable-key, driving-table, bounded-scan, grouping, and
incremental-safety proofs before rendering changed-key predicates.

`FullMaterializationPurpose` resolves the same authoritative macro snapshot and normalizes the
complete read-only query without applying changed-key restrictions. Full creation, validation, and
refresh all pass through this purpose. If Atlas cannot compile it, the query remains valid for
interactive execution but materialization creation is ineligible.

`InteractiveQueryPurpose` resolves stored macros and applies only independently proven interactive
rewrites, without materialization restrictions. Queries such as `SELECT *` and calls to unknown
ordinary functions remain valid interactive SQL. An unresolved `macros.*` definition raises the
structured optimization exception so the execution caller can run the original SQL unchanged.
Projection pruning and repeated-expression rewrites belong to this purpose policy and reuse the
same resolved scope graph.

The first interactive rewrite pushes an authored `WHERE` conjunct through direct projection aliases
into a single-use CTE or derived table while retaining the original outer predicate. It declines
the rewrite across limits, offsets, grouping, distinctness, qualification, windows, computed
projection columns, shared CTEs, duplicate output names, and volatile or unknown functions. These
barriers keep the rewrite equivalent while still exposing selective predicates closer to managed
physical scans.

Interactive rewrite safety is distinct from SQL validity. A closed taxonomy recognizes proven
deterministic row-local functions such as casts, null handling, string normalization, arithmetic
helpers, and scalar conditionals. Predicates composed from those functions may move through
projection aliases, join equivalence, grouping-key proofs, window partitions, and pre-`UNNEST`
inputs. Unknown functions remain valid DuckDB SQL but do not move.

Anonymous or unclassified typed functions, random/UUID generation, clock and session values,
sequence access, aggregates, windows, subqueries, and row-expanding functions fail the predicate
rewrite proof. This same classification prevents otherwise safe structural rewrites from crossing
an unapproved scalar evaluation boundary.

Interactive inner joins also derive equivalent `WHERE` predicates through qualified `ON` column
equalities and `USING`. Equivalence is transitive across an inner-join chain, and every derived
predicate remains alongside the authored predicate before pass-through-scope pushdown runs. Left,
right, full, cross, and non-equijoin relationships do not contribute equivalence edges.

For a left join, a predicate originating on the preserved input may constrain an equal nullable-side
column without becoming a parent `WHERE` predicate. `ON` joins retain the derived constraint inside
the join condition; `USING` joins retain their merged-column semantics and receive the constraint
inside a safe single-use child scope or a filtered physical-table wrapper. Predicates originating
on the nullable side never propagate to the preserved side, including null-rejecting predicates.

An outer filter over a single-use positional `UNION ALL` is also copied into every branch when each
referenced output maps positionally to a direct projected column. Explicit set-output names are
honored, existing branch filters are retained, and the outer filter remains observable in its
authored position. The rewrite is atomic: if any branch has a computed or missing output, no branch
is changed. Deduplicating unions, `UNION ALL BY NAME`, shared set scopes, global set modifiers, and
volatile or unknown predicates remain barriers.

An outer predicate on a direct grouping-key output may be copied into the aggregate scope's input
`WHERE` clause. The compiler resolves direct output aliases and DuckDB grouping ordinals, retains
existing input filters and `HAVING`, and leaves the authored outer predicate in place. Predicates on
aggregate results do not move. Computed grouping keys, grouping sets, rollups, cubes, shared scopes,
and volatile or unknown expressions are barriers because filtering their input can change emitted
groups or observable evaluation.

The same grouping-key proof applies to standalone `HAVING` conjuncts. A conjunct that references
only direct ordinary grouping keys is copied into the input `WHERE`, including null predicates,
while the complete authored `HAVING` remains in place. Aggregate-dependent conjuncts and boolean
expressions that mix grouping keys with aggregates are not decomposed or moved. This preserves
group membership and three-valued logic while exposing the selective predicate before aggregation.

For a single-use window scope, an outer predicate may move into the input only when every referenced
output is a direct column common to every inline window's partition key. Such a predicate is
constant within each partition, so filtering selects complete partitions and preserves window
values, frames, ordering, and `QUALIFY`. The outer predicate remains in place. Global windows,
predicates on non-partition columns, incompatible multi-window partitions, named windows, computed
partition expressions, and shared scopes remain unchanged. Generic projection pushdown also treats
every inline window as a barrier; only this narrower partition proof may cross it.

Projection pruning removes unused direct-column outputs from CTEs, derived tables, and expanded
table macros. Requirements are collected from every consumer, so a shared CTE retains the union of
all columns used for results, filters, joins, grouping, aggregate arguments, ordering, and windows.
Pruning repeats to a fixed point: once an outer scope drops an output, its input can shed newly
unused columns too. Query-boundary traversal includes function and aggregate arguments but never
leaks dependencies across a nested scope.

Wildcards, duplicate or explicit positional output names, projection ordinals, `DISTINCT`, and
volatile or unknown-function scopes remain unchanged. Computed outputs are retained even when
unused because removing their evaluation could suppress an observable error or side effect; direct
column reads have no such evaluation boundary.

Redundant derived tables are collapsed before predicate derivation when they contain only unique
direct-column projections over one physical relation. Column references are rewritten through every
rename, while explicit aliases preserve the derived scope's outward result names. The replacement
retains the derived alias, so qualified joins—including nullable outer-join inputs—keep their
binding and nullability. Nested pass-through scopes collapse recursively before the remaining
interactive rules rebuild lineage.

CTEs remain evaluation and materialization boundaries. Wildcards, `USING` joins, filters,
aggregation, ordering, limits, computed or duplicate outputs, explicit positional names, hints, and
other select modifiers also prevent scope elimination. Those constructs either expose the derived
schema directly or carry semantics and directives that cannot be discarded with the wrapper.

Projection `UNNEST`, cross-join `UNNEST`, and lateral `UNNEST` can filter their pre-expansion base
row early. A qualified predicate that depends exclusively on that base relation is copied into its
safe derived input or into a filtered wrapper around an ordinary physical table; the authored
post-expansion predicate remains. This applies to left lateral expansion as well, because filtering
the preserved input by a predicate that is retained after expansion selects the same input rows and
does not alter null extension.

Predicates that reference expanded values, mix pre- and post-expansion columns, contain subqueries
or functions, or use ambiguous unqualified columns remain after expansion. Positional table-alias
column lists and unmanaged/table-function scans are also barriers because Atlas cannot map authored
names back to physical columns without a coherent catalogue metadata proof.

An inner `ORDER BY` without `LIMIT` or `OFFSET` is removed only under an exact observability proof.
Every consumer must project unique direct columns and order by every output, whether by output name,
source reference, or ordinal. Rows tied by that complete ordering are identical as observable
results, so the discarded inner ordering cannot change their visible sequence. The proof is checked
across every consumer of a shared scope.

Partial consumer ordering, wildcards, aggregates, windows, computed outputs, volatile or unknown
consumer expressions, and complex inner ordering keys retain both sorts. CTEs explicitly declared
`MATERIALIZED` or `NOT MATERIALIZED` also retain their order because the compiler preserves authored
evaluation directives independently of whether DuckDB could optimize them further.

A literal outer `LIMIT` with an optional literal `OFFSET` may bound an ordered child early when the
outer scope is a single-source, row-preserving direct projection. The child must order every unique
direct output, making tied rows observationally identical. Atlas adds `LIMIT outer_limit +
outer_offset` after that child ordering and retains the authored outer limit and offset, so the
selected prefix and final slice remain exact.

Unordered or partially ordered children, outer filters or joins, computed projections, shared
scopes, parameterized bounds, existing child bounds, and explicitly materialized or
non-materialized CTEs remain unchanged. `ORDER BY ... LIMIT` in the outer scope belongs to the
separate Top-K proof because moving its ordering requires column-lineage rewriting.

For `ORDER BY ... LIMIT`, the compiler can push a complete Top-K plan into a single-use child. The
outer scope must be a row-preserving direct projection whose ordering covers every unique output.
Atlas rewrites each ordering key through projection aliases—including direction and null
placement—then adds that order and `LIMIT outer_limit + outer_offset` to a direct-column child. The
authored outer order, limit, and offset remain, so ties, membership, and final presentation are
unchanged.

Partial outer orders, filters or joins in the outer scope, computed child outputs, shared scopes,
parameterized bounds, complex existing child sorts, explicit positional output contracts, and CTE
materialization directives are barriers. Requiring direct child outputs avoids suppressing
evaluation errors or side effects for rows outside the pushed Top-K.

Repeated derived scans are shared when their normalized definitions are identical, deterministic
direct-column projections over one ordinary physical table with an optional rewrite-safe filter.
For a root query that totally orders every direct output, Atlas hoists repeated instances into one
collision-free generated `MATERIALIZED` CTE and replaces each instance with a reference retaining
its authored relation alias. The scope graph is rebuilt immediately, so later projection and join
rules analyze the shared definition normally.

The total root ordering makes any physical-order change from reuse unobservable. Partial ordering,
limits or offsets inside the repeated scan, computed or volatile outputs, unknown functions,
external/table-function scans, correlation, and non-identical definitions remain separate. Atlas
does not infer that merely similar SQL is equivalent.

For an authored CTE without an explicit materialization directive, the interactive compiler can
make the reuse decision explicit when the final query totally orders every direct output. A
deterministic direct-column scan over one ordinary table, with an optional rewrite-safe filter, is
marked `NOT MATERIALIZED` when referenced once and `MATERIALIZED` when referenced more than once.
This avoids an unnecessary evaluation boundary for single-use scans and shares repeated work.

Authored `MATERIALIZED` and `NOT MATERIALIZED` directives are never changed. Partial final ordering,
recursive or correlated definitions, computed or volatile outputs, unknown functions, and
external/table-function scans leave an implicit CTE unchanged. These barriers ensure that changing
physical evaluation or row order cannot become observable.

Exact repeated deterministic scalar expressions in the root projection—including expressions
produced by scalar-macro expansion—can be evaluated once per input row. For a single ordinary-table
query whose final ordering covers every unique output, Atlas introduces a collision-free scalar
`CROSS JOIN LATERAL` projection and replaces each repeated projection with its qualified result.
Every authored output name remains unchanged.

The initial proof excludes filters, joins, limits or offsets, partial ordering, grouping,
aggregates, windows, volatile or unknown functions, and unmanaged scans. A scalar lateral
projection always emits exactly one row, while the complete final ordering makes any physical row
order change unobservable. Atlas only shares exact normalized expressions; it does not assume that
merely similar expressions are equivalent.

The shared interactive execution boundary validates the authored statement before invoking the
compiler. Both the HTTP workbench and analytics tools use this boundary. When compilation returns
SQL, that SQL is sent to the bounded Quack runtime while the authored statement remains the public
query identity. When compilation raises `QueryOptimizationUnavailable`, the boundary executes the
already validated authored SQL unchanged. It does not catch compiler defects or execution errors,
and optimization never creates an alternate validation or execution route.

At registration, the same boundary records an `optimization_status` in the existing expiring query
state. `optimized` means the compiler emitted a structural rewrite, `unchanged` means compilation
succeeded without one, and `degraded_fallback` means optimization was unavailable and the authored
SQL is executing. The state does not expose rewritten SQL and remains operational telemetry rather
than durable query history.

Interactive compilation also has an auxiliary explained result for Atlas execution adapters. It
contains the compiled SQL plus an ordered, bounded list of stable rewrite identifiers and proof
evidence. A rule is recorded only when its execution changes the normalized query tree; merely
checking a rule does not produce an explanation. The primary `compile_catalogue_query()` contract
continues to return SQL or raise.

The public execution boundary copies those explanations into the existing expiring query state.
For example, predicate pushdown records that direct projection lineage preserved the predicate,
while repeated-expression sharing records that exact deterministic projections used an equivalent
row context. Unchanged and degraded-fallback executions carry no applied rewrites. Internal
rewritten SQL is not exposed through query status.

The same query-state record carries bounded non-blocking optimization diagnostics. Existing
interactive lint warnings such as an unbounded result are recorded before compilation. If the
compiler falls back, its structured code, message, and documentation anchor are appended as a
warning while the validated authored SQL continues to execute. Applied rewrites and diagnostics
remain separate: the former explain proven changes, while the latter explain performance risks or
why no optimization was available.

## Supported subset

The first purpose is keyed materialization. It currently proves:

- direct driving-table scans;
- inner equijoins and `JOIN ... USING` whose stable keys reach every scan;
- equijoin-dependent scans beneath an explicit `MATERIALIZED` driving CTE,
  including joins through non-key identifiers;
- key-bounded left and right joins whose driving table remains on the
  preserved side;
- key-bounded full joins with complete merged or explicitly coalesced
  stable-key output;
- nested, non-recursive SELECT CTEs and derived tables with direct projection lineage;
- explicit CTE column lists with positional output-name lineage;
- key-preserving `UNION`, `INTERSECT`, and `EXCEPT`, including `ALL` and
  name-aligned union variants;
- exact-key aggregation, including `GROUP BY ALL`, using proven deterministic
  aggregate functions;
- plain `DISTINCT` when the unchanged stable key remains in the result;
- deterministic `DISTINCT ON` when it partitions exactly by the stable key and
  ordering determines every projected value;
- `ORDER BY` without a membership-changing `LIMIT` or `OFFSET`;
- stable-key-partitioned aggregate, ranking, and deterministically ordered
  row-choice windows;
- projection and lateral `UNNEST` over already bounded row-local expressions;
- FROM-free deterministic lateral scalar projections over aliases already
  available to their left;
- single-relation `*`, explicitly qualified `relation.*`, and wildcard modifiers
  that leave every stable key untouched;
- unchanged stable-key projections;
- named row-local expressions and predicates using proven deterministic functions;
- null-safe scalar and composite key filtering.

For keyed and append refresh, recursive CTEs, global or non-key grouping, unproven full-join forms,
unresolved macros, and ambiguous or schema-dependent wildcard projections currently raise an
optimization diagnostic. The materialization caller then uses correct result-level key filtering,
which may scan more data. Append refresh uses the same bounded stable-key plan as keyed refresh,
alongside its separate insert-only CDC checks. Full refresh uses the broader full-materialization
purpose instead of these incremental restrictions.

## Deterministic functions

The compiler accepts arithmetic, casts, `CASE`, and an explicit set of known row-local DuckDB
functions. Unknown functions and macros decline optimization until their definitions can be
inspected. Random values, UUID generation, current clock or session values, and sequence access also
decline repeated-execution rewrites.

## Macro expansion

The compiler receives an immutable snapshot of authoritative Postgres macro definitions. Known
`macros.*` scalar calls expand recursively into expression trees before deterministic-function
validation. Known table macros expand into derived SELECT scopes before projection and join lineage
analysis. Positional arguments, named arguments, and stored table-macro defaults are supported.

Authored and stored SQL remain ordinary DuckDB SQL; expansion exists only in the compiler plan.
Materialization workers load one definition snapshot and pass it into the compiler rather than
querying mutable catalogue state during analysis. Missing definitions, invalid signatures,
unparseable bodies, and excessive recursive nesting produce `unsupported_function`, allowing the
caller to run the original SQL with degraded performance.

## Managed relations

Keyed materialization can scope only relations owned by the coherent Atlas catalogue snapshot.
DuckDB dynamic relations such as `query` and `query_table`, file and object readers such as
`read_parquet` and `read_csv_auto`, and external database scan functions are explicitly
incompatible. They raise `unmanaged_relation` before ordinary function validation.

The diagnostic identifies the relation function but replaces its arguments with `...`; paths,
connection strings, and embedded SQL are not copied into warnings or status records. Other
non-external table functions retain the generic unsupported-function fallback until they receive a
separate boundedness and side-effect proof.

## Stable output key

Keyed optimization requires at least one unique declared key column. Every key must appear in the
result under the same name and trace through unchanged column references. Direct aliases inside
CTEs and derived tables preserve lineage; expressions and casts of a key use the generic fallback.

## Wildcard projections

The compiler can prove declared stable-key lineage through a wildcard without knowing every column
in the source schema. An unqualified `*` is accepted when its SELECT scope has exactly one relation.
A qualified `relation.*` is accepted when that relation resolves unambiguously in the scope. These
rules compose through ordinary CTE and derived-table boundaries.

`EXCLUDE`, `REPLACE`, and `RENAME` are also accepted when their explicitly named modifications do
not remove, transform, rename, or shadow any declared stable-key column. Expressions introduced by
`REPLACE` still pass through the normal deterministic-function and query-shape proofs.

An unqualified wildcard across multiple relations is ambiguous without source schema metadata.
Pattern-filtered stars, key-touching modifiers, and wildcard expansion paired with an explicit CTE
column list can omit, transform, or positionally rename the stable key. Those forms decline
optimization with the `wildcard-projections` diagnostic rather than guessing. The authored SQL
remains valid for interactive use but is not eligible for materialization.

## Scope lineage

The compiler traverses non-recursive SELECT scopes from the innermost query outward. Each physical
scan, CTE, and derived table has its own relation boundary. Direct projections connect input
columns to output names across that boundary, including aliases, while joins connect equivalent
columns within a scope. This lets a stable key propagate through nested scopes without requiring
users to annotate their SQL.

An explicit CTE column list renames the child SELECT outputs positionally. The compiler requires
the list to name every output exactly once, then connects each inner output to its declared boundary
name. Computed or dropped keys, recursive scopes, non-SELECT scope bodies, and malformed column
lists decline optimization. The caller retains the original valid SQL and uses its documented
fallback.

## Outer joins

A left or right join can be scoped when every physical scan has stable-key lineage and the driving
table is on the preserved side. Atlas injects the changed-key predicate inside every scan,
including the nullable input, rather than adding a result predicate that could remove unmatched
preserved rows. Additional `ON` predicates remain untouched.

If the driving table is anywhere beneath the nullable branch—even through a CTE or derived
table—the compiler declines optimization.

A full join is key-local when every declared stable key appears in `USING`, every physical input
can therefore be bounded to the same changed-key batch, and the SELECT projects each merged,
unqualified key exactly once. DuckDB's merged `USING` value preserves the key for unmatched rows
from either side, and that lineage continues through CTE and derived-table boundaries.

A single direct root `FULL JOIN ... ON` is also supported when qualified equijoin lineage bounds
both physical sides and every result key explicitly uses `COALESCE(left_key, right_key)`. The two
arguments may have different physical names, and composite keys are proved independently. Keeping
this proof at the root avoids pretending that ordinary equality lineage can represent a value with
alternative origins.

Full joins with incomplete composite keys, qualified one-sided key projections, nested or chained
`ON` forms, or other key expressions decline optimization. Accepting direct lineage from either
nullable side would incorrectly assign null to unmatched rows.

## Set operations

Every `UNION`, `INTERSECT`, and `EXCEPT` leaf independently passes the ordinary driving-table,
output-key, function, join-lineage, and scan-bounding proofs. Because the stable key is part of
every result row, equality, duplicate removal, multiset counts, intersection, and subtraction
cannot couple rows belonging to different keys.

For positional operators, the compiler additionally proves that each stable key occupies the same
output ordinal in both branches. For name-aligned unions, branches may reorder columns or omit
non-key columns, but each branch must expose every stable key under its declared name. The compiler
reconstructs the authored distinct or `ALL` operation from the optimized leaves. If any leaf
declines optimization, the entire authored query remains intact for caller fallback.

Global `ORDER BY` is retained because it changes presentation but not membership. Global `LIMIT`
and `OFFSET` remain incompatible. A non-recursive set-root `WITH` clause is propagated into each
compiled leaf using only that leaf's transitive CTE dependency closure. Definitions retain authored
order, unused definitions cannot create false driving scans, and recursive or shadowed-name cases
fail closed. Positional wildcard outputs still require source-schema metadata.

## Keyed aggregation

An aggregate scope is bounded only when its grouping columns correspond one-to-one with the
declared stable-key lineage. Atlas scopes every proven physical scan before aggregation, causing
`COUNT`, `SUM`, `AVG`, `MIN`, and `MAX` to recompute the complete group for each changed key.
`HAVING` remains in its authored position, so a group that stops satisfying it correctly
disappears after the materialization runtime deletes and recomputes that key.

The same complete-group proof supports order-insensitive boolean and bitwise reductions,
`COUNT_IF`, `MEDIAN`, population and sample variance/deviation, correlation, and population and
sample covariance. The compiler recognizes these functions explicitly rather than treating every
DuckDB aggregate as incrementally safe.

Order-sensitive `LIST`, `STRING_AGG`, `FIRST`, `LAST`, and `ANY_VALUE` are accepted when their
aggregate-level `ORDER BY` includes the exact emitted or selected value expression. Earlier sort
expressions may refine the ordering; once the value itself participates, any remaining tie consists
of identical values and cannot change the collection or chosen result. Single-expression
`DISTINCT` collections use the same proof.

`ARG_MIN` and `ARG_MAX`, including literal top-N forms, use the same principle. Their ordering
expression must determine the emitted value. A tuple such as `(score, title)` therefore safely
selects `title`, while ordering by `score` alone declines optimization because equal scores may
select different titles.

For DuckDB `GROUP BY ALL`, the compiler derives the implicit grouping set from non-aggregate
result expressions. Constants do not create a grouping dimension. Every column-bearing implicit
grouping expression must still be an unchanged stable-key column, and the inferred set must contain
each declared key exactly once.

Global aggregation, additional grouping dimensions, transformed grouping expressions, ambiguous
unqualified grouping columns, unordered collections, independently ordered row choices, `MODE`, and
other unproven aggregates decline optimization. Those shapes cannot yet prove that one declared
result key identifies exactly one complete recomputation group or one deterministic chosen row.

## Key-local distinct

Plain `DISTINCT` is safe when the result preserves the declared stable key unchanged. The key
partitions duplicate elimination: rows belonging to one changed key cannot deduplicate against a
different key. Atlas therefore scopes every physical scan first and performs ordinary DuckDB
deduplication over each complete changed-key partition.

`DISTINCT ON` is safe when its partition expressions map one-to-one to the declared stable key and
its ordering determines every projected value. A projected expression is determined when it
appears directly in `ORDER BY` or every source column it depends on is directly ordered. Constants
and stable-key projections need no additional ordering. This admits aliases and positional order
references while ensuring any remaining tie yields an identical result row.

Cross-key partitions, under-specified row ordering, and wildcard output decline optimization.
Wildcard row choice requires source schema metadata to prove all expanded result values.

## Ordering

`ORDER BY` without `LIMIT` or `OFFSET` is accepted in any otherwise proven SELECT scope. The
compiler retains the authored ordering while injecting stable-key predicates into physical scans;
the bounded rewrite changes neither row membership nor duplicate semantics. Materialized tables do
not promise persistent row order, but accepting the clause preserves ordinary DuckDB execution
semantics and avoids rejecting valid user SQL unnecessarily.

Any global or nested `LIMIT` or `OFFSET` remains incompatible with keyed recomputation because a
changed row can alter selected membership outside the changed-key batch. Global ordering attached
to a set-operation tree is retained because it changes presentation rather than membership.

## Key-local windows

A window is key-local when every partition includes every declared stable-key lineage column.
Additional partition dimensions are safe because they only subdivide a complete changed-key
partition. Atlas scopes the physical inputs first, then preserves the authored window ordering,
frame, and `QUALIFY` evaluation.

The supported order-insensitive aggregate catalogue can be used as window functions alongside
`RANK`, `DENSE_RANK`, `PERCENT_RANK`, and `CUME_DIST`.

`ROW_NUMBER`, `LAG`, `LEAD`, `FIRST_VALUE`, `LAST_VALUE`, and `NTH_VALUE` are supported when every
row-choice window in a scope shares the same stable-key partition and ordering. That ordering must
determine every visible non-window value and every selected value or default; offsets must be
non-negative integer literals. Direct window projections preserve row identity, and identical
remaining ties therefore produce the same result multiset. This includes the common
`ROW_NUMBER() ... QUALIFY position = 1` pattern.

`NTILE` uses the same partition and ordering proof with a positive integer literal bucket count.
Rows that remain tied after a value-determining order are visibly identical, so exchanging their
hidden identities cannot change either the result multiset or the distribution of bucket values.

Named `WINDOW` definitions are resolved into their effective inline specifications before these
proofs run. Acyclic inheritance is supported, while undefined names, cycles, and attempts to
override an inherited partition, ordering, or frame fail closed.

Global windows, partitions missing any stable-key component, mixed row-choice orderings, dynamic
offsets or bucket counts, and indirect window projections decline optimization.

## Row-local expansion

Projection `UNNEST`, `CROSS JOIN UNNEST`, and explicit `LATERAL UNNEST` are supported when their
arguments are deterministic row-local expressions. A lateral expansion may reference only aliases
already available to its left. `UNNEST` is modeled as a row-multiplying scope rather than a
physical catalogue scan, so Atlas injects stable-key predicates into the owning scans and leaves
DuckDB to expand each bounded row.

A FROM-free lateral `SELECT` is also supported when every named projection and optional `WHERE`
depends only on aliases already available to its left. It produces at most one row per bounded
input row, supports `CROSS` and `LEFT` joins, and may compose with earlier lateral projections.
The declared stable key must still be projected unchanged from the owning relational scope.

Subqueries inside `UNNEST` arguments, lateral SELECTs with independent relations, aggregation,
windows, or row expansion, unrelated table functions, and dynamic/external relations decline
optimization. Those constructs need separate proofs for correlation, physical ownership, snapshot
consistency, cardinality, and side effects.

## Join lineage

For inner joins, the compiler builds column-equivalence classes from `USING` columns and qualified
column equalities in `ON`. Ordinarily it rewrites only when every physical scan has a proven binding
for every driving key. A dependent scan may instead join through a non-key identifier when the
driving scan is isolated in an explicit `MATERIALIZED` CTE and column lineage proves that the
dependent scan is below that barrier. `NOT MATERIALIZED`, implicit CTE policy, independent scans,
and nullable-side driver paths do not receive this exception. Otherwise the compiler raises
`unbounded_relation` and materialization is ineligible.

During bounded execution, the materialization purpose supplies the selected key rows and the
compiler renders them as typed direct literal predicates—including native UUID literals—in every
directly bound scan. This is required for current DuckLake partition pruning. Validation can
compile against the changed-key relation contract without reading data.

## Interactive document scoping

`elements` is the exceptional interactive relation whose global logical row count makes an
unchanged fallback unsafe. Users still write ordinary DuckDB SQL. The compiler accepts either:

- a finite literal `document_id` predicate at every physical `elements` scan; or
- an inner-equijoin lineage from every `elements.document_id` to a selective managed source whose
  canonical relationship targets `documents.document_id`.

For the second form, compilation returns a typed document-scope plan as well as executable SQL. The
plan resolves distinct document IDs from the element-free source, performs a second literal
`documents.document_id IN (...)` lookup to obtain the authoritative `element_count` budget, and
replaces every physical `elements` alias with one shared temporary relation containing only the
columns used by the query. Runtime executes scope
resolution, hydration, and the user query on one session-affine Quack client inside one snapshot
transaction that performs no lake mutation, so every stage observes one DuckLake snapshot.

The default ceiling is 10,000 documents and 50,000,000 elements. Scope resolution requests one
extra document so an over-budget result is rejected deterministically before DOM hydration.
Unprovable or over-budget scans fail closed with `unbounded_relation`; this is the deliberate
exception to interactive authored-SQL fallback. The compiler never infers scope from names,
arbitrary joins, or user hints.

## Test-driven extension

Each new feature begins as a structured incompatibility case. Its implementation must add:

1. a plan-structure test proving the intended lineage;
2. a semantic test comparing the original and compiled query for bounded keys;
3. negative cases for joins, nulls, aliases, and other boundaries affected by the feature.

Tests should assert plan structure and row equivalence instead of relying primarily on generated SQL
snapshots.

## Upstream boundaries

When safe compilation depends on DuckDB, DuckLake, DuckBasin, or Quack behavior, record the minimal
reproduction and required upstream contract in `UPSTREAM.md`. Compiler diagnostics should link the
corresponding documentation anchor; Atlas-specific SQL workarounds are not part of the user
contract.
