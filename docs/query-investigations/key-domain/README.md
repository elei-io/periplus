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

## Compound inner joins: production validation (2026-09-09)

Classification: **compiler/optimizer eligibility gap**. A required content-key
conjunct proves the same document connectivity as a standalone equality; additional
join conditions need not disable content scoping. The pass now traverses parenthesized
AND expressions to find that equality, in either direction, and retains the complete
original ON expression. It never treats an equality inside OR as required. The
existing expression whitelist, inner-join restrictions and installed-definition
checks remain in force. No schema, physical layout, materialization or runtime limit
changes accompany this extension.

The representative production query joins capture, heading and section with both
`content_id` equality and `heading_node_index = node_index`, filters capture URL by
`%.gov%` and heading text by `%artificial intelligence%`, then orders by capture time.
Its submitted plan has no content-scoping diagnostic and computes the heading and
section branches before joining the filtered captures. Estimates include 8,895,977
text-node rows and 1,041,463 heading inputs; these are not measured cardinalities.

Validation used a separate local macOS ARM64 DuckDB 1.5.5 process with production
SELECT/GetObject-only credentials, a read-only QueryService attachment, two threads,
488.2 MiB memory and 244.1 MiB spill. Metadata traffic used a temporary Kubernetes
port-forward; S3 reads went directly to the configured endpoint. The live query
process was not changed. These are remote-storage diagnostic timings, not production
query-pod latency or a controlled speedup benchmark. Each statement had a 20-second
interrupt timer. Variants within each case used one transaction and snapshot;
concurrent ingestion and materialization continued between cases.

Snapshot 46017 contained 13,790 captures, 12,810 prose rows and 4,052 distinct content
IDs matching the URL predicate. The new generator matched the installed catalogue.
The full representative query still timed out after scoping at snapshot 46035.

Narrow selection used the effective URL of the first `.gov` capture ordered by
content ID (`00024fb9b64e2bcb6ffe9f657b1f18d844c4f2bfe7d52309deac1d4b57e5792b`).
Each query below starts with `capture c`, joins its extraction on content identity
plus the stated residual ON predicate, and filters `c.effective_url`. The empty case
uses `https://absent.invalid/periplus-scope-probe`. Scoped ran first, then original;
a scoped timeout ended that case without running the original.

| Extraction / residual | Selection | Snapshot | Scoped | Original |
| --- | --- | ---: | --- | --- |
| Headings + sections / matching heading node | One URL | 46089 | 20.108 s timeout | Not run |
| Metadata / `name = 'title'` | One URL | 46113 | 1.434 s, 1 row | 20.080 s timeout |
| Sections / `parent_heading_node_index IS NULL` | One URL | 46128 | 0.665 s, 1 row | 20.095 s timeout |
| Metadata / `name = 'title'` | `%.gov%` | 46155 | 20.066 s timeout | Not run |
| Headings / `level = 1` | Empty | 46180 | 0.642 s, 0 rows | 20.025 s timeout |

Original timeouts prevent complete production result equivalence claims for these
pairs. Local differential fixtures cover all 13 reviewed views in both join
directions, selective/empty/full driver domains, residual NULL/false/OR conditions,
duplicate captures, nested conjunctions, parameter positions, ordered output and
complete section boundaries. Disjunctive-only key matches, disconnected keys,
outer joins and volatile/explicit-cast predicates remain ineligible. A locked
read-only QueryService test verifies prep/execute activation and standalone reuse.

The extension repairs a syntax-dependent omission across the reviewed views. It
does not solve the production heading query, prove proportional file pruning, or
justify another persistent projection by itself. Broad discovery and the remaining
heading reconstruction costs need separate measured investigation.

A follow-up at snapshot 46205 compared the new ON-conjunction path with the already
supported equivalent WHERE-conjunct scoping path, in one transaction. Metadata and
section rows and column descriptions matched exactly (one row each). This validates
the extension against the existing scoped implementation on production data; it
is not a completed unscoped-baseline comparison. The selected document was 303,016
bytes. EXPLAIN ANALYZE reported one title group, one section-window row, and one
group for a separate `html_heading` query with `level = 1`. Each HTML scan in these
profiles read one file; the heading/section element scan emitted 5,590 rows before
heading filtering. Capture-side documents still read 63–64 files, and the
fulfillment aggregation emitted 14,828 groups. These profiles establish selective
HTML computation and some remaining unrelated capture work. Fast standalone heading
and section profiles do not explain the combined heading/section timeout.

`make check` passed: 641 Python tests (34 skipped), five SDK tests, shared-package
checks/tests, and both frontend checks and builds. Production deployment was not
changed by this validation.

A subsequent non-executing EXPLAIN of the scoped one-URL heading/section join
identified an additional computation barrier: DuckDB introduced
`__common_subplan_1` over the heading-filtered html_elements scan, estimated at
1,050,272 rows, with each consumer applying its content-key semijoin *after* that
shared CTE. The heading aggregation and section windows are downstream of the
semijoins, but constructing the shared CTE still lacks the selected key domain.
This is concrete plan evidence of remaining avoidable work, not measured time
attribution. It motivates a separate investigation of common-subplan extraction
and filter propagation; the current change does not disable native optimizers or
force wide primitive CTE materialization.

## Remaining timeout: shared inputs and broad scans (2026-09-09)

Classification: **compiler/optimizer** for the shared intermediate; a separate
broad-scan cost remains under investigation. Read-only diagnostic connections used
the same local host, reader credentials, two threads, 512 MB/256 MB configured
memory/spill limits and remote storage access described above. Changing
`disabled_optimizers` affected only those disposable connections; no deployed
setting or public deadline changed.

At snapshot 46504, the scoped one-URL combined heading/section query completed
EXPLAIN ANALYZE in 1.308 seconds with only `common_subplan` disabled. It produced
one heading group and one section-window row; each HTML scan read one file.
The default optimizer run, second in the same transaction/snapshot, timed out at
20.097 seconds. Thus the default run already followed a cache-warming execution.
This isolates a severe regression associated with common-subplan extraction on
this selective workload. It is a single paired diagnostic, not a general argument
for globally disabling that optimizer.

The [standalone reproduction](common-subplan.sql) uses only ordinary in-memory
DuckDB tables: 1,000 content keys, ten elements and 100 text rows per key, and one
selected key. With default optimization, a shared CTE constructs all 10,000
element rows and both consumers filter it afterwards. Disabling `common_subplan`
lets each element scan emit ten rows with a dynamic content-key filter. The ten
result rows have identical multisets in both variants; ordering is unspecified.
The reproduction requires no Periplus, DuckLake or network. The production query's
shared CTE has the same placement relative to its consumer semijoins.

This does not explain the entire broad-query timeout. With `common_subplan`
disabled, the original `.gov` / artificial-intelligence workload still timed out
at 20.085 seconds on snapshot 46531. Further independently bounded profiles,
also with that optimizer disabled, timed out as follows:

| Operation after selecting distinct `.gov` content IDs | Snapshot | Deadline outcome |
| --- | ---: | --- |
| Count text nodes, without reading text values | 46583 | 20.012 s timeout |
| Count text nodes and sum text lengths | 46604 | 20.042 s timeout |
| Reconstruct and filter headings, without sections | 46623 | 20.085 s timeout |
| Compute sections, without heading text | 46640 | 20.038 s timeout |

These results establish that the broad workload can exceed the deadline before
heading reconstruction or section windows are required. They do not by themselves
distinguish storage latency, bytes scanned and semijoin processing. Do not attribute
all broad-query time to ordered string aggregation or solve it solely by changing
the compound-join recognizer.

The count-only diagnostic at snapshot 46665 also exceeded a 60-second local
deadline (60.015 s). That longer allowance was diagnostic only. The operation did
not complete, so no actual broad-scan file count or per-operator timing is claimed.

Physical metadata exposed a substantial small-file population:

| Active relation | Data files | Total data-file bytes | Delete files |
| --- | ---: | ---: | ---: |
| `material.html_nodes` | 4,618 | 702,623,022 | 66 |
| `material.html_elements` | 2,428 | 778,731,118 | 67 |
| `ingest.documents` | 64 | 2,845,357 | 0 |
| `ingest.visits` | 2 | 1,427,011 | 0 |

The HTML relations average approximately 152 KB and 321 KB per data file. Many
small remote files are a strong explanation to investigate for scan/open overhead,
but these metadata counts are not the number actually read by the timed-out query.
The broad selected hash domain can reach all eight content buckets; bucket layout
alone cannot promise a cheap scan for that domain.

Read-only maintenance inspection found `/readyz` returning 503 with
`treatment_blocked`, worker-ready 0, worker-stuck 0, two failure-blocked treatments,
2,716 successful merges and 14 failed merges over the worker's lifetime. Thus
maintenance is not wholly stopped. Logs include compaction commit conflicts on
table 99 (`html_elements`) with concurrent transactions deleting from that table;
those historic conflicts are not proof of the current admission cause.

The `python -m lakeducktor select` diagnostic at snapshot 46883 identifies the
current capacity constraint. It reports merge pressure with:

- `html_elements`: 2,383 eligible input files, 280,919,551 input bytes, eight groups.
- `html_nodes`: 4,574 eligible input files, 276,676,182 input bytes, eight groups.
- `link_occurrences`: 3,048 eligible input files, 152,031,862 input bytes, one group.

Selection ends with `selection=none reason=no_treatment_fits_memory
memory_deferred=3` under one thread and a 512,000,000-byte DuckDB budget. This is a
memory-admission decision, not a measured out-of-memory failure. Other smaller
relations continue receiving work. The inventory establishes a real compaction
backlog and an actionable maintenance constraint, without proving that increasing
memory alone is the correct remedy.

Next steps are to repair bounded compaction/admission in LakeDucktor (which owns
physical maintenance), then rerun the broad scan and full query; separately, address
the native common-subplan/filter-propagation regression using the reproduction.
Do not add Periplus-owned compaction or another public materialization solely to
conceal these issues. A global optimizer disable, higher public deadline, schema
change or production maintenance action was not applied during this investigation.

## Plan-aware warnings and targeted alternative evaluation (2026-09-09)

The app now identifies the observed shared-producer barrier from bounded native
JSON EXPLAIN evidence for the row-capped executable. It emits
`content_scope_shared_input` while retaining the SQL and native optimizer settings.
`content_scope` describes adding restrictions without promising a bounded scan;
unrecognized/oversized plan evidence emits `content_scope_plan_unverified`.
Compiler version is `public-query-v3`. The new detector is tested independently
and against actual parser-derived plans, including complete section windows and
producer cardinalities. A real read-only DuckLake QueryService fixture verifies
warning delivery through both preparation and execution; integration tests also
check private preparation evidence and unchanged locked optimizer settings.

The [repeatable reader probe](../../../packages/periplus/scripts/query_common_subplan_probe.py)
compares the scoped SQL with normal optimization and with only `common_subplan`
disabled in a disposable connection. Two repetitions reverse variant order; each
successful pair shares a transaction/snapshot. A failed variant ends that pair.
Complete result comparison is capped at 100,000 rows / 32 MiB, with no partial
result-equivalence claim. Output omits SQL, parameter values and result rows.

The same local ARM64 reader setup and 512 MB / 256 MB configuration produced:

| Case | Snapshot | Default execution | Without common_subplan | Complete equivalence |
| --- | ---: | --- | --- | --- |
| One URL, alternative first | 47558 | 20 s deadline | 1.247 s, 1 row | Not established |
| One URL, default first | 47579 | 2.603 s, 1 row | 0.697 s, 1 row | Yes |
| `.gov` + heading phrase | 47586 / 47610 | 20 s deadline | 20 s deadline | Not established |
| Full-domain combined extraction | 47628 / 47655 | 20 s deadline | 20 s deadline | Not established |
| Empty, alternative first | 47682 | 2.422 s, 0 rows | 0.748 s, 0 rows | Yes |
| Empty, default first | 47685 | 2.611 s, 0 rows | 0.580 s, 0 rows | Yes |

Reported execution durations exclude the separately recorded EXPLAIN time, while
the 20-second interrupt covers both. Timers may finish slightly after the deadline.
The broad rows combine separate interrupted pairs and are not same-snapshot timing
comparisons. Compaction/ingestion and cache state could change between pairs;
these are not isolated production-pod benchmarks. The default plans consistently
triggered the new shared-input finding; the alternatives did not.

A separate production-data preparation using the updated, configuration-locked
QueryService completed in 0.134 seconds and returned `content_scope`,
`content_scope_shared_input`, and `plan_truncated`. Thus the warning survives a
truncated human-readable preview. `lock_configuration` remained true and
`disabled_optimizers` remained empty. No execution or deployment occurred in that
preparation check.

The alternative preserves results in all 39 local view/selectivity differential
cases, and improves the completed selective production-data pairs. The broad
cases remain unresolved. Consequently this evaluation does not activate automatic
optimizer selection, globally disable common-subplan extraction, relax connection
locking, or force wide intermediate materialization. The benchmark and plan
regressions make that subsequent decision reviewable without changing current
production security or execution policy.

Final validation passed `make check`: 650 Python tests (34 skipped), five SDK tests,
shared-package checks/tests, and both frontend checks and builds. The reader probe
also passed compilation and CLI validation. Application changes remain local and
were not deployed as part of this work.

## Compaction-progress recheck (2026-09-10)

Repeated the reader-only investigation while LakeDucktor catches up. No application,
optimizer, infrastructure or lake data changes were made by this recheck. Unrelated
working-tree changes were preserved. Measurements use the same local ARM64 access
path, two query threads and 512 MB / 256 MB memory/spill configuration as before;
they are not production-pod latency measurements.

At snapshot 54192, active element files had fallen from 2,428 to 233 (943,459,433
bytes), and node files from 4,618 to 2,752 (888,219,433 bytes). The corpus had grown
to 16,149 captures and 14,682 prose rows; the `.gov` predicate selected 5,105 distinct
content IDs, versus 4,052 in the earlier inventory. There were 77 element delete
files and 78 node delete files. Thus the physical state and logical workload have
both changed; file-count reductions are not a controlled timing comparison.

The maintenance selection report at snapshot 54206 confirms a 2 GB DuckDB budget.
It still identifies 160 eligible element files in eight merge groups and 2,633
eligible node files in eight merge groups. Compaction is making substantial
progress, particularly on elements, but the node-file backlog remains large.

The existing paired probe reversed variant order between repetitions. Completed
pairs share a snapshot and compare complete result multisets and column types;
interrupted pairs do not establish equivalence.

| Workload | Snapshot | Default scoped plan | Without common_subplan | Complete equivalence |
| --- | ---: | --- | --- | --- |
| `.gov` + heading phrase | 54236 / 54272 | 20 s deadline | 20 s deadline | Not established |
| One URL, alternative first | 54308 | 2.983 s, 1 row | 1.079 s, 1 row | Yes |
| One URL, default first | 54318 | 2.930 s, 1 row | 0.714 s, 1 row | Yes |
| Full-domain combined extraction | 54322 / 54359 | 20 s deadline | 20 s deadline | Not established |

The broad rows combine separate interrupted snapshots, not same-snapshot pairs.
The deadline includes preparation; successful execution durations above exclude
EXPLAIN. In both completed one-URL pairs the default plan still contained the
unrestricted shared HTML producer and the alternative did not. This confirms the
remaining selective optimizer cost, but does not establish that disabling this
pass solves broad extraction as compaction progresses.

The unmodified representative SQL also timed out at 20.201 seconds on snapshot
54391. A separate count-only query over `.gov` text nodes, with a selected-key
semijoin and no heading/section computation, timed out at 20.015 seconds on snapshot
54423. No broad EXPLAIN ANALYZE completed, so actual files read and operator-time
attribution remain unavailable. Broad primitive access still exceeds the budget
while node compaction is incomplete; the current evidence does not isolate a
post-compaction heading-reconstruction bottleneck. No additional runtime rewrite,
settings change or deployment was made. Only this investigation record changed;
`git diff --check` passed.

## Sixty-second recheck (2026-09-10)

After the operator increased the live execution policy, the control API confirmed
60 seconds, 1,000 rows and 16 MiB. The reader-only diagnostic retained two threads,
512 MB memory and 256 MB spill. Initial active-file inventory was 353 element files
(952,188,293 bytes) and 1,299 node files (952,726,433 bytes). Concurrent ingestion
and maintenance mean these are changing snapshots, not a controlled before/after
benchmark.

The count-only `.gov` text-node query completed `EXPLAIN ANALYZE` in 52.939 seconds
at snapshot 55066. Its selected-key relation contained 5,218 content IDs. The node
scan read all 1,299 files and emitted 20,067,891 text rows; the semijoin retained
9,575,926 rows. Its cumulative operator time was 93.469 seconds across two threads,
not elapsed wall time. The documents scan read 128 files and accumulated 10.094
operator seconds. A two-second native stack sample during this diagnostic showed
both execution threads predominantly inside Parquet/S3 range reads and network
polling. This establishes storage waits during the sample, not a full-query I/O
percentage or production-pod latency.

| Local diagnostic | Snapshot | Outcome |
| --- | ---: | --- |
| Original representative SQL | 55172 | Interrupted at 60.143 s |
| Content-scoped SQL | 55282 | Interrupted at 60.140 s |
| Content-scoped SQL, common_subplan disabled | 55384 | Interrupted at 60.039 s |

These broad interrupted runs do not establish result equivalence. Disabling the
optimizer pass remains insufficient for this workload on the diagnostic access
path. Classification remains both catalogue cost and optimizer behavior: content
scoping does not make the broad primitive scan selective at file access, while the
previous completed selective cases separately demonstrate avoidable common-input
work. This run does not establish the residual heading/section computation cost
after compaction or justify a new materialization by itself.

After the diagnostic connection closed, the original SQL was submitted through
the existing production query process at its authenticated `/query/exec` endpoint.
It returned HTTP 200 in 38.072 seconds wall time (38,034.230 ms service elapsed),
one row, no truncation, snapshot 55469, no diagnostics, and unchanged returned SQL.
No result values or credentials were emitted by the probe. This used the live
service's normal connection and configured policy, not a second DuckDB process in
the pod. Consequently the local 60-second timeouts must not be presented as
production endpoint failures. The raised limit unblocks this representative
execution; one successful run does not establish a latency distribution or broad
query-family success rate. No runtime code, policy or deployment was changed by
this recheck; only this evidence record was updated.
