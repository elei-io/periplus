# Request-scoped headings and titles: experiment

Classification: compiler/optimizer provisionally; physical access remains unmeasured.
The query history cohort selected one crawl request before joining grouped HTML
views, but failed despite small expected output. Two representative executions:
`dfae3980-f48e-4462-b9e2-1a2d2e8c0da4` (titles) and
`0ca03297-02a6-4630-aa97-4b3ffa3c2d5b` (first heading and prose prefix).
Private SQL remains only in the disposable reader pod during the investigation.

Hypothesis: materializing the original selected-capture CTE, deriving distinct
content IDs and restricting complete HTML primitive inputs before extraction
avoids corpus-wide aggregation. All original joins, predicates and output ordering
remain. Heading text retains all descendant text nodes. Installed public view
definitions are checked in the same read transaction using ContentScope.matches.

Bench: shared benchmarking.measure_pair, production reader image 508b2bb,
DuckDB 1.5.5, two threads, 256 MB spill, isolated 4 GiB pod, one read transaction
per comparison, no production limits or catalogue changes. First executions are
not cold-cache measurements. No file-pruning claim without profiles.

Initial findings:

- Title baseline: snapshot 108760, first execution 98.472 s, zero rows; total
  120 s budget exhausted during first warm repetition. No completed pair.
- Heading candidate materializing all primitive columns: 512 MB out of memory.
- Heading candidate retaining only required primitive columns: snapshot 109057,
  23.582 s, 60 rows at 512 MB; baseline in the same transaction ran out of memory.
  This is completion evidence, not result equivalence.
- Heading baseline at equal 1 GB diagnostic budget: snapshot 109269, out of memory.

No production optimizer change or PR is accepted from these incomplete pairs.
- Heading baseline at equal 2 GB diagnostic budget: snapshot 109393, out of memory.
  Raising the diagnostic budget did not establish a complete baseline. Production
  resource settings were unchanged.
- Completed title pair at 512 MB, snapshot 109406, candidate first: candidate
  14.039 s versus baseline 119.445 s (8.51x faster). Complete output types and
  digests match, but both returned zero rows. This is a single-order timing,
  not repeatability or non-empty semantic coverage. Earlier baseline-first run
  exhausted its total repetition budget before reaching the candidate.

## Profile and decision

On later frozen snapshot 109555, the narrow heading candidate returned 111 rows
in 27.445 s; its warm EXPLAIN ANALYZE took 44.576 s. Original query again ran out
of memory at the same snapshot and 512 MB budget. Result counts across snapshots
are not compared for equivalence: ingestion/materialization continued.

The candidate selected 134 content IDs, retained 975 heading elements and 33,109
text nodes for extraction. However, physical scans still emitted 67,603,025
element rows and 53,392,497 text rows before later filtering. The HTML scans
reported 209 element files and 205 node files. Query profile reported 3,146,303,808
bytes read and 406,149,708 cumulative rows scanned; the latter is an engine scan
counter, not unique DOM nodes. Buffer high-water counter exceeded the configured
DuckDB budget, so successful execution in a 4 GiB reader pod does not establish
safety in the production query pod's 1 GiB container.

Verdict: promising execution improvement, not ready to ship. No PR/deployment.
The broad-read bottleneck remains, and complete non-empty baseline/candidate
result equivalence has not been established. Next isolate exact same content keys
as literals versus a key relation, then inspect file/row-group pruning. This
separates runtime filter propagation from physical layout. Add non-empty,
small-scope differential fixtures before designing automatic eligibility.

Temporary reader pod and its private SQL/profile payloads are deleted at the end
of the experiment. Only sanitized aggregate observations remain here.

## Follow-up: literal versus relation keys

Same-snapshot access-only probes on snapshot 109651 selected 136 distinct content
IDs from the original heading query's capture CTE. Standard shared bench,
512 MB, two threads, 256 MB spill; one transaction for all variants. Both orders
were tested for one key and the complete key set, over html_node (text nodes)
and html_element. Outputs were per-content counts and summed text lengths: these
matched with identical types in all eight pairs. This is access-path validation,
not full heading-result equivalence.

| Access | Node profile | Element profile |
|---|---|---|
| One key, literal or joined | ~28 ms; 293,039 bytes; 11 files | ~25–28 ms; 117,903 bytes; 12 files |
| 136 literal IN keys | 10.8–19.1 s; ~2.04–2.05 GB; 244 files | ~8.4 s; ~1.72 GB; 247 files |
| 136 joined keys | 18.5–26.2 s; ~2.10 GB; 258 files | ~7.2–7.4 s; ~1.72 GB; 262 files |

Ordinary warm-ish single-key executions were ~23–32 ms. First-connection/cache
conditions differ, so profile timings are not service-latency guarantees. IN
versus join had no consistent overall advantage. A single content key already
has a useful access path; multi-key access reads broadly in both forms.

The node scan's rows_scanned counter remained 260,438,720 even for a one-key
scan emitting only 178 rows and reporting 293 KB read. Therefore this counter
must not be interpreted as unique rows actually decoded or processed. Files,
bytes and operator output cardinality are needed alongside it. This corrects
any stronger interpretation of earlier corpus-sized scan counters.

On snapshot 109680, the same comparison protocol contrasted 136-key IN against
explicit equality disjunctions (OR), both orders, text-node counts/lengths. All
outputs matched. Both scan forms emitted 52,778,312 rows and reported 216 files
and ~2.058 GB. IN profiles took 15.7/21.3 s and OR 24.4/19.8 s. No repeatable
improvement; rewriting IN into OR does not solve this case.

### Separate equality scans (UNION ALL)

A diagnostic UNION ALL of 136 single-ID aggregate reads preserves distinct-key
count/length semantics. It is not a proposed public rewrite. Both paired runs
matched summary outputs/types at their respective pinned snapshots.

| Order / snapshot | IN ordinary / profile | Separate reads ordinary / profile | IN bytes / separate bytes |
|---|---|---|---|
| Separate first / 109724 | 11.520 / 20.593 s | 10.272 / 6.007 s | 2,062,922,745 / 58,763,822 |
| IN first / 109740 | 9.127 / 11.789 s | 18.975 / 17.965 s | 2,068,890,573 / 58,763,822 |

Separate scans emitted 48,680 text rows versus ~53 million from the IN scan.
They reported 3,497 / 3,224 summed file accesses across 136 scans, not distinct
files. IN reported 246 / 233 files. Scan counters summed across the UNION multiply
corpus estimates and must not be compared as actual processed-row ratios.

Conclusion: existing physical data supports substantially more selective reads
(~35x fewer reported bytes), but naive decomposition does not give repeatable
latency gains and is rejected for deployment. The optimization target is efficient
multi-key selective access, preserving equality pruning while amortizing repeated
planning/file access. Physical layout may still affect per-key overhead; these
measurements do not establish corpus-independent scaling or isolate a specific
DuckDB optimizer pass. The next useful engine reproduction should expose exact
key-set filtering at the scan boundary and compare it to equality scans. Keep
full heading correctness/activation tests separate from these access summaries.

No production code/configuration changed. Disposable reader and private payloads
removed after completing this follow-up.

## Exact scan membership investigation

Production-reader image 6ca8878, snapshot 109807. Raising
`dynamic_or_filter_threshold` from the installed default 50 to 1000, with 136
selected keys, did not solve access. Order 50/1000/1000/50 produced ordinary times
6.99/12.35/22.19/18.88 s and profile times 10.73/26.30/16.42/20.09 s. Default read
~2.14 GB and emitted 54,629,621 rows; 1000 read ~2.09 GB and emitted 52,776,740.
All 136 per-content counts/length totals and types matched. No setting deployed.

DuckDB v1.5.5 `JoinFilterPushdownInfo::PushInFilter` explicitly wraps generated
membership in OptionalFilter for zonemap pruning. Source:
https://github.com/duckdb/duckdb/blob/v1.5.5/src/execution/operator/join/physical_hash_join.cpp
Configuration reference:
https://duckdb.org/docs/stable/configuration/overview

`multikey_repro.py` is a standalone synthetic Parquet reproduction, no Periplus
or production data. DuckDB 1.5.5, 2 million rows sorted by content ID, 136 keys,
2048-row Parquet groups. Scan output: join 1,971,700 rows; IN 268,288;
IN plus redundant `list_contains([keys], content_id)` 13,600 exact matching rows.
All aggregate outputs equal, with the native optimizer and with in_clause disabled.
This isolates exact row membership from coarse statistics pruning. Synthetic
row-group size is intentional diagnostic layout, not a production layout proposal.

### Combined statistics and exact membership filter

On snapshot 110089, literal IN versus the same IN plus
`list_contains([same literal keys], content_id)`, 136 keys, both orders:

| Order | IN ordinary / profile | Combined ordinary / profile |
|---|---|---|
| IN first | 13.582 / 11.567 s | 8.207 / 0.821 s |
| Combined first | 18.854 / 22.602 s | 2.747 / 1.787 s |

Both scans reported 222 files. IN emitted 53,423,931 rows and reported
~2.083–2.085 GB profile bytes; combined emitted 48,680 rows and reported
3,748,101 bytes. Both per-content count/length outputs and types matched.
The byte metric is reported by warm EXPLAIN ANALYZE, not independently measured
S3 network traffic. These are isolated reader timings, not public API latency.

Unlike separate equality scans, the combined candidate preserves one scan and
shows gains in both orders. This does not prove file-count independence from
corpus growth. Public queries deriving keys from captures still require a safe
way to supply exact runtime membership; this literal-key probe does not implement
that compiler/service boundary. No production rewrite or setting deployed.

Full primitive-row comparison on snapshot 110320 selected
(content_id,node_index,value), ordered by content_id,node_index. All 48,680 rows,
values, multiplicities, column names and types matched by the shared bounded
result digest in both orders. IN-first ordinary times: 10.183 s IN, 7.685 s
combined. Combined-first: 0.930 s combined, 16.392 s IN. No profiling in this
comparison. This closes primitive-row equivalence for these keys, not full
heading/capture query equivalence or general compiler eligibility.

## Query-derived keys and complete heading query

The one-statement candidate added
`list_contains((SELECT list(content_id) FROM selected_keys), content_id)` to
scoped HTML primitives. It ran out of memory at 512 MB on snapshot 111500.
The synthetic reproduction likewise emitted 1,971,700 scan rows for this dynamic
expression versus 13,600 for a constant list; summary outputs remained equal.

A two-stage diagnostic retained one read transaction: execute the original
capture selector, deduplicate non-null content IDs, then supply exact membership
to extraction. Restricting HTML alone failed at 512 MB and 1 GB. EXPLAIN confirmed
membership reached the HTML scans; prose still lacked the restriction. Adding
it to prose allowed the complete heading query to finish at 512 MB.

Snapshot 112043, 136 selected IDs, selection 0.824 s:

| Order | Earlier scoped-HTML reference | Exact HTML + prose candidate |
|---|---|---|
| Candidate first | 24.890 s | 3.953 s |
| Reference first | 59.502 s | 13.318 s |

Both pairs returned the identical complete 136-row heading/prose result and
column types. Conservative totals adding selection to each candidate are
4.777 s and 14.142 s (~5.2x and ~4.2x faster). The reference is the earlier
research scoped-HTML rewrite, NOT untouched public SQL. Selection was run once
within this transaction; charging its measured cost to both candidates is
accounting, not two independently timed end-to-end requests.

A further diagnostic used one bound array parameter, not generated SQL with
literal IDs: `content_id IN (SELECT unnest($selected_ids)) AND
list_contains($selected_ids, content_id)`. The original capture selector supplied
the array in the same transaction. Snapshot 112591: selection 0.744 s, complete
heading candidate 3.072 s, 136 rows (~3.816 s combined). Untouched original SQL
then ran out of memory at the same snapshot/budget, so no equivalence claim for
that incomplete pair. This proves the bound-array execution path is viable for
this small scope, not a production QueryService implementation.

### Scale guard evidence

`membership_scale.py` uses synthetic sorted Parquet, 100,000 contents × 20 nodes,
two threads, bound key arrays, one ordinary execution per shape (not statistically
stable performance acceptance). Baseline is a key relation join via IN/UNNEST;
candidate adds exact list_contains.

| Selected IDs | Baseline ms | Candidate ms |
|---|---|---|
| 136 | 69.8 | 38.3 |
| 1,000 | 112.9 | 148.3 |
| 10,000 | 670.2 | 1,148.0 |
| 100,000 (entire corpus) | 6,234.3 | 9,861.9 |

All count/length results matched. This is selected-scope growth, not unrelated
corpus growth, and does not establish a universal numeric cutoff. Broad scopes
regress; never apply this unconditionally. The public service needs bounded key
selection, parameter-byte and key-count limits, one shared operation deadline and
snapshot, conservative eligibility/selection tests, and multi-family validation
before an experimental implementation is accepted. Existing prep does not execute
analytical discovery, so selection belongs in execution, not prep. No change to
that boundary, deployment, PR, or optimizer activation was made by these probes.

Decision: selective two-stage execution has concrete complete-query gains, but
all release gates have not passed. Do not ship a universal membership rewrite.
Private SQL remained in the disposable reader, deleted after this experiment.

## Experimental implementation and acceptance update

The user explicitly accepted modest broad-query slowdowns in exchange for
previously failing queries completing. `query/selected_content.py` implements
bounded selection and guarded expansion; QueryService invokes it only during
experimental execution, after binding the original and validating installed views.
The collection limits are 100,000 capture-key rows / 8 MiB of distinct key bytes, under the existing operation
snapshot/deadline. Stable remains native for these shapes. Prep never selects.
Unsupported grammar retains normal execution; independent title aggregation CTEs
are not covered by this initial implementation.

Changed QueryService in an isolated reader over production data, DuckDB 512 MB,
256 MB spill, two threads, no result truncation:

| Case | First reader (4 GiB container) | Repeated at production-sized 1 GiB container | Rows |
|---|---|---|---|
| Original request-scoped heading/prose query | 6.114 s (113302) | 10.600 s (113449), 7.412 s (113483) | 136 |
| Request-scoped metadata | 2.443 s (113308) | 3.295 s (113466), 2.815 s (113501) | 4,976 |
| Request-scoped sections | 3.990 s (113312) | 8.081 s (113470), 5.636 s (113503) | 1,125 |

These service runs are completion/reliability evidence across changing snapshots,
not differential equivalence claims between snapshots. Earlier same-snapshot
comparisons and local adversarial differential tests provide equality evidence.
The live endpoints have not been changed by running a patched disposable reader.

Tests cover actual installed DuckLake views through QueryService; stable/prep
nonactivation; selection timeout and recovery; collection bounds; source definition
mismatch; anonymous bindings including request UUIDs and non-selector parameters;
duplicate and null capture keys; empty selection; outer joins; grouped first
headings and prose; and complete section boundaries. Local fixture tests compare
full rows and column descriptions against the unmodified SQL in three families.

Final-source verification at the same 1 GiB container cap: heading 11.188 s
(snapshot 113808), metadata 2.670 s (113826), sections 7.915 s (113828), all
untruncated with the same respective row counts. Temporary reader deleted.
Full `make check` passed: 744 backend tests (34 environment-dependent skips),
5 SDK tests, shared package checks/tests, and both frontend checks/builds.
