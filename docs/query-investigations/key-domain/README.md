# Requested-key propagation through derived relations

Investigation: 2026-09-09. Classification: **compiler/optimizer**, with a separate
physical-pruning limitation. The observed work is avoidable without changing the
public schema or storing metadata. This does not establish that every future
unbounded extraction can be made inexpensive.

## Finding

A selective relation is joined to a derived relation computed independently per
key, but the required keys reach that relation after its aggregation or window.
The general optimization is semijoin reduction / sideways information passing,
often described as a magic-set transformation for non-recursive SQL. A useful
name for a prep-layer pass is **requested-key propagation**.

The important invariant is partition independence. If a computation F is local
to key K and D is the demanded set of keys, then:

```text
F(R) SEMI JOIN D ON K = F(R SEMI JOIN D ON K)
```

For a multi-input computation, restrict each input whose key lineage participates
in the same partition. Keep the original outer result join: the semijoin only
restricts work and must not replace capture multiplicity with DISTINCT results.
This is not a rule about prose, titles, HTML, or a column spelled content_id.

## Actual environment and plan evidence

DuckDB 1.5.5, Linux ARM64 query container, read-only QueryService connection,
2 threads, 488.2 MiB memory and 244.1 MiB spill (the configured 512 MB/256 MB).
The corpus contains 5,544,801 HTML nodes, 3,039,462 elements and 1,478 prose rows.
Measurements below used snapshot 8458. Execution was through a separate read-only
connection inside the query container, with a 25-second interrupt per variant;
full results were retained for equality checking rather than HTTP result caps.

The user's original query (`m.name = 'title'`) returns 334 rows. Prose selects
289 content IDs and joins to 303 captures. The metadata title branch nevertheless
aggregates all 1,478 titles; its text scan emits 920,483 rows and reads all eight
node files. The outer plan estimate of roughly 558,458 rows is not actual output.

`m.kind = 'title'` removes the other metadata UNION branches, but returns 303 rows,
not 334: it is a different query, not a legal transparent optimization. Its title
branch still computes all 1,478 titles.

An added IN/semijoin at the metadata-view boundary and a lateral wrapper both
preserve the original result, but neither reduces the title aggregation. A CTE
or correlation in SQL does not inherently establish early execution.

## Broader experiment

The read-only [live probe](../../../packages/periplus/scripts/query_key_domain_probe.py)
loads the existing SQL for metadata, headings, and sections. It applies the same
experimental restriction to their primitive inputs. These three views were
manually verified to be content-local; **the probe is not an eligibility analyzer
or a production optimizer**. It uses a query-local selected relation, distinct
key domain, semijoins on primitive sources, and otherwise retains the view SQL.
Primitive CTEs use NOT MATERIALIZED so repeated references can retain separate
column and predicate pushdown.

Each view was tested with `'%robot%'` (289 content IDs), `'%wild robot%'` (8), and
`'%'` (all 1,478). Both CTE strategies and all nine cases returned identical
multisets and column types at the same snapshot, including duplicate captures.
The selected relation is evaluated during execution, never by the prep endpoint.

Observed operator output cardinalities from EXPLAIN ANALYZE:

| Work | Original | 289-key scope | 8-key scope |
| --- | ---: | ---: | ---: |
| Metadata title groups | 1,478 | 289 | 8 |
| h1 text groups | 1,866 | 393 | 8 |
| Section window rows | 39,457 | 7,968 | 91 |

The eight-key metadata experiment reduces the text scan's emitted rows from
920,483 to 321. It still reads five of eight files. With 289 keys, all eight files
remain relevant. These are scan outputs and file counts, not proof that only
321 physical rows were read. In particular, the profile's rows-scanned counters
were twice logical table counts on this two-thread DuckLake attachment; do not
interpret those counters as unique rows or exact storage I/O.

After one warm-up pair, three measured pairs alternated original/scoped order on
one connection and snapshot. Medians in milliseconds:

| Derived relation | Match | Original | Scoped |
| --- | --- | ---: | ---: |
| Metadata | robot | 668 | 645 |
| Metadata | wild robot | 321 | 259 |
| Metadata | all | 914 | 785 |
| Headings | robot | 228 | 239 |
| Headings | wild robot | 254 | 150 |
| Headings | all | 217 | 232 |
| Sections | robot | 157 | 146 |
| Sections | wild robot | 151 | 110 |
| Sections | all | 184 | 185 |

These are small local trials, not production estimates. The operator reductions
are clear, but elapsed-time improvements are modest and not universal. An initial
implicitly materialized primitive CTE also made one narrow heading trial slower
(220 to 769 ms). Sharing wide intermediate tables can undo predicate/projection
pushdown. A blanket rewrite is not justified by these measurements.

## Independent reproduction and engine boundary

[reproduce.sql](reproduce.sql) runs in an empty, disposable ordinary DuckDB database.
It uses account groups, events, and eight requested accounts, with no HTML,
Periplus, DuckLake, credentials, or network access. The ordinary grouped join
computes 1,000 account groups; explicit input semijoins reduce that to eight.
A partitioned ranking example shows the same reduction (DuckDB lowers its rank
limit to grouped arg_min internally). This establishes an optimizer-class issue,
not merely a missing Periplus relation.

The pinned [DuckDB join-filter optimizer source](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.5/src/optimizer/join_filter_pushdown_optimizer.cpp)
already supports pushing dynamic filters through grouping keys. However, its
join traversal visits only the first child and does not remap equivalent key
bindings to the other input; WINDOW is not a supported traversal case there.
Our title plans have a RIGHT join below the aggregate: the grouped content key
originates in the other child. The observed missing filter is consistent with
that limitation. This is not a claim that dynamic filtering never crosses an
aggregate, or that enabling a disabled optimizer switch would solve it.

The technique and need for cost-based choice are established in
[Cost-Based Optimization for Magic: Algebra and Implementation](https://www.cse.iitb.ac.in/~sudarsha/Pubs-dir/costbased-magic-sigmod96.pdf).

## What belongs in prep

A viable general pass needs three pieces, in order:

1. **Bound lineage and partition-locality proof.** Derive identities from resolved
   columns and expressions, not column names or a hand-maintained list of HTML
   relations. Expand authoritative view definitions only when their semantics
   are understood and match the installed catalogue. Infer preservation through
   projections, filters, UNION ALL branches, grouping keys, and complete window
   partitions. For joins, prove the matching key equality; for a left join,
   retain preserved-side key provenance and unmatched rows. A grouping set that
   omits the key is not partition-local. Global windows, global limits, recursive
   queries, opaque macros, volatile functions and unproved correlations stop the
   transformation. Error-raising expressions also need care: removing evaluation
   from discarded groups can change observable failures.
2. **Generate a bounded alternative.** Materialize only the selected driver once
   as a query-local CTE; semijoin its key domain below proven computation barriers.
   Keep final multiplicity, output names/types, ordering and null semantics.
   Resolve aliases and positional parameters before copying expressions. Do not
   introduce persistent tables, execute discovery during prep, or split the
   query into HTTP calls/snapshots. Keep one statement in the existing transaction.
3. **Explain both alternatives and select conservatively.** Use bounded compile
   effort and retain unchanged SQL when ineligible or not demonstrably better.
   Physical EXPLAIN estimates here are inaccurate, so summing estimated rows is
   not a reliable cost function. An initial implementation should operate on
   well-proved shapes and have a regression corpus spanning selectivity and data
   distribution before automatic activation. If no safe improvement is available,
   a diagnostic can describe the late selective join without rejecting valid SQL.

A superficial IN wrapper, automatically marking every CTE MATERIALIZED, or
special-casing metadata would not satisfy this design. Neither would a universal
"push all content_id filters everywhere" rule: it can corrupt section boundaries,
window ranks, cross-document computations, outer joins and duplicates.

The current prep layer has SQLGlot syntax validation and a truncated textual
physical plan, not a bound relational IR with lineage or a trusted cost model.
Consequently **no production rewrite was enabled in this investigation**. The
smallest defensible implementation is a bounded, proof-driven pass for this
operator class, backed by differential and activation tests—not another full
schema-dependent compiler. Improving the native engine's bound-plan propagation
is the cleaner long-term boundary and would benefit direct DuckDB users too.


## Implemented bounded subset

The subsequent implementation uses a reviewed catalogue `content_local` contract instead
of building the general bound-lineage engine discussed above. Exact activation rules and
validation are documented in [QUERY.md](../../QUERY.md#reviewed-content-scoping-in-query-prep-2026-09-09).
The original experiment above remains an investigation record. Automatic activation is
limited to this reviewed subset and does not claim cost-based plan selection.

The actual generator was compared against original public SQL on snapshot 8458, in one
read-only transaction using the normal two-thread, 512 MiB query connection. All 19 cases
preserved column descriptions and complete result multisets: each opted-in view with
`%wild robot%`, and metadata/headings/sections additionally with `%robot%` and `%`.
The broader views include zero-result cases in this corpus; nonempty fixture coverage
checks every opted-in view separately.

| Extraction | Pattern | Actual extraction rows, original → scoped | Execution seconds, original → scoped |
| --- | --- | --- | --- |
| Title groups | `%robot%` | 1,478 → 289 | 0.615 → 0.592 |
| Title groups | `%wild robot%` | 1,478 → 8 | 0.296 → 0.241 |
| Heading groups | `%robot%` | 39,457 → 7,968 | 0.655 → 0.322 |
| Heading groups | `%wild robot%` | 39,457 → 91 | 0.607 → 0.153 |
| Section window | `%robot%` | 39,457 → 7,968 | 0.209 → 0.149 |
| Section window | `%wild robot%` | 39,457 → 91 | 0.162 → 0.110 |
| Heading groups | `%` | 39,457 → 39,457 | 0.656 → 0.684 |
| Section window | `%` | 39,457 → 39,457 | 0.163 → 0.183 |

These are single sequential pairs, original first, with concurrent host test activity.
They verify reduced computation and illustrate latency variability; they are not a
controlled speedup estimate. An earlier version retained the prose predicate above the selected CTE and measured
0.608 → 0.839 seconds for robot titles despite fewer title groups. Moving that predicate
entirely into selection avoids retaining prose text solely for a repeated test; the
table shows the final generator, validated again across all 19 cases.
Full-domain searches pay key-set overhead without reducing extraction. This is an
accepted limitation of the small initial rule, not evidence for inventing an estimated
row-count cost model.
