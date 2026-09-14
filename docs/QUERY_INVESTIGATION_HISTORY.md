# Historical query investigations

These dated investigations describe superseded and historical implementations.
For the current contract and developer workflow, read [QUERY.md](QUERY.md).

### Historical validation of removed HTML views

The following dated measurements describe the superseded catalogue. Specialized
HTML views are no longer installed in either query schema. They are retained here
as historical investigation evidence, not current query examples.

### HTML table view validation (2026-09-08)

The first html_table_cell view shape had an optimizer/predicate-pushdown issue:
correlated text extraction and span expressions admitted unrelated content scans,
despite a query selecting one content and one table. The public cell grain was
already bounded to the chosen table and did not require corpus-wide layout.
The SQL view now uses filterable grouping keys, inlined row/cell inputs, ordinary
text joins and a scalar list fold for grid placement. No query API rewrite,
physical relation, or materialization was introduced.

Representative query:

```sql
SELECT * FROM public_v1.html_table_cell
WHERE content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'
  AND table_node_index = 165
ORDER BY row_index, column_index;
```

Read-only local-lake validation at snapshot 7527 used 5,448,418 node rows and
2,977,143 element rows. The selected book-details table returned 14 source cells.
The initial plan admitted 2,629,871 element rows from an unrelated-content scan.
The revised EXPLAIN ANALYZE plan places the exact content hash in every primitive
scan, emits 35 text nodes, 7 source rows, 14 source cells and one table into their
respective operations; the row-parent scan emits 211 elements of that content.
Each primitive scan read two Parquet files.
Observed local execution was 0.15–0.24 seconds (warm/cache-sensitive), versus about
1.05 seconds for the first shape. This is evidence for filtered on-demand use,
not a billion-row scalability or unfiltered-corpus performance claim. DuckLake's
profile rows-scanned counter exceeded the source row count, so it is not used as
a physical I/O measure here. Reassess with production file counts and workloads
before deciding whether to materialize.

Fixtures verify that content/table filters isolate malformed unrelated tables,
while filtering output rows still accounts for preceding row-spanning cells.

A separate synthetic 100-row by 10-column table returned all 1,000 expected cells
in 0.20 seconds on a local in-memory fixture. Local deployment verification
confirmed both public DESCRIBE contracts and the same 14-cell book table through
the public HTTP query gateway. Public documentation includes the relations and
parameterized extraction example.

### Heading view validation (2026-09-08)

The html_heading view uses an ordinary content-keyed subtree join and ordered text
aggregation. No schema or optimizer performance defect was observed in the initial
filtered validation, and no compiler rewrite or physical materialization was added.

```sql
SELECT node_index, level, text FROM public_v1.html_heading
WHERE content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'
ORDER BY node_index;
```

The local book page returned 10 headings in 0.24 seconds. EXPLAIN ANALYZE showed the
exact content predicate on both primitive scans, each reading two Parquet files.
The scans emitted 228 text nodes and 211 elements from that content before the
heading/subtree filters and aggregation. This validates the filtered query shape;
it is not a guarantee for unfiltered corpus queries or billion-row deployments.

### Metadata view validation (2026-09-08)

Initial filtered validation found no schema or optimizer performance defect.
The original html_metadata used inlined primitive scans and UNION ALL, with an
ordered text join only for titles. No compiler rewrite or materialization was added.
Selecting the book content hash used in the heading example returned 12 metadata
rows in 0.21 seconds locally, including its title, description, language and raw
relative stylesheet/icon URLs. EXPLAIN ANALYZE showed the exact content predicate
on each primitive scan. This validates filtered on-demand access, not unfiltered
corpus performance. Declaration, null/empty, repetition, multi-token rel and
foreign-namespace behavior are covered by real HTML parser fixtures.

The [title direct-text investigation](query-investigations/title-direct-text/README.md)
uses the stored HTML title text to remove that reconstruction without changing
metadata declarations or adding a compiler rewrite.

### Image view validation (2026-09-08)

html_image is a direct projection/filter over html_element. Initial filtered
validation found no schema or optimizer performance defect; no rewrite or
materialization was added. Selecting the book content hash used above returned
seven images in 0.05 seconds locally. EXPLAIN ANALYZE showed exact content and
img predicates on the single primitive scan, emitting seven rows and reading two
Parquet files. URLs remained relative and undeclared dimensions remained null.
This validates the filtered shape, not unfiltered corpus-scale performance.

### JSON-LD view validation (2026-09-08)

Initial filtered validation found no schema or optimizer performance defect.
The original html_jsonld implementation joined selected script nodes to their
direct text children and used TRY_CAST to retain parser failures as rows. It adds no compiler rewrite or
physical materialization and does not depend on the private JSON-LD projection.

```sql
SELECT node_index, value, parse_error FROM public_v1.html_jsonld
WHERE content_id = '072a5a77e6bf9d591a30832addace1b20ccfa7e4e13b1bd9a00a3779b7bce252'
ORDER BY node_index;
```

A captured Probot article returned one complete @graph document without a parse
error in 0.17 seconds locally. EXPLAIN ANALYZE placed the exact content predicate
on both primitive scans, each reading two files and emitting one row. This is
filtered-query evidence, not a guarantee for unfiltered corpus workloads.
Fixtures cover complete arrays/graphs, repeated scripts, invalid/empty declarations,
JSON null, type matching and foreign namespaces.

The follow-up [direct-text investigation](query-investigations/jsonld-direct-text/README.md)
removes that redundant reconstruction using existing `html_element.text_direct`.
The public columns and parse semantics remain unchanged.

### List view validation (2026-09-08)

Initial filtered validation found no schema or optimizer performance defect.
html_list counts direct items for reversed defaults; html_list_item uses ordered
windows for numbering resets and subtree joins for text. No compiler rewrite or
materialization was added. Selecting the book content hash used above returned
10 list items in 0.18 seconds locally. EXPLAIN ANALYZE put the exact content
predicate on every primitive scan, each reading two files. The text scan emitted
276 nodes, direct-item scans 10 rows each, and list-candidate scans 211 elements
before tag filtering. This is filtered-query evidence, not a corpus-wide guarantee.
Fixtures verify that filtering later output items preserves earlier value resets.

### Form views validation (2026-09-08)

The initial form-control ownership query had an optimizer predicate-pushdown
issue: the selected content was bounded, but conditional outer joins admitted
574,409 ID-bearing elements and 4,572 forms from unrelated content. Splitting
explicit-reference and ancestor ownership into disjoint UNION ALL branches
preserved the public semantics and let DuckDB push content predicates into every
scan. This was an optimizer issue, not a reason to materialize the relations.

For content 01c41839fd99a13cc60ada13d53a98fb5ad75fa65f70f64edae0acb5ee1eba61,
`SELECT * FROM public_v1.html_form_control WHERE content_id = ? ORDER BY node_index`
returned 12 controls in 0.25 seconds locally (initial shape: 0.77 seconds).
EXPLAIN ANALYZE showed each primitive scan reading two files; the ID scan emitted
18 elements and the form scan one form. Option extraction for content
072a5a77e6bf9d591a30832addace1b20ccfa7e4e13b1bd9a00a3779b7bce252 returned four
options in 0.09 seconds, with content predicates on all scans and two files each.
These timings validate filtered local queries, not billion-row corpus workloads.
No query API rewrite, stored projection or form execution was added.

### Section view validation (2026-09-08)

Initial filtered validation found no schema or optimizer performance defect.
html_section computes preceding-parent and following-boundary windows over
HTML headings, with the document-root node providing the final boundary. It
adds no stored projection or compiler rewrite. Selecting the book content hash
used above returned 10 passages in 0.11 seconds locally. EXPLAIN ANALYZE put the
exact content predicate on both primitive scans, each reading two files: one
root node and 211 element candidates before heading filtering. Product Description
was [154, 161), ending at Product Information; its parent was heading 112.
This validates filtered local use, not unfiltered corpus-scale performance.
Fixtures verify that a heading filter retains the surrounding headings needed
to compute its parent and end, plus all six ranks and empty passages.

### Link sort-order comparison (2026-09-08)

Classification: potential physical-layout mismatch, not an established optimizer
failure. Public joins select links by capture identity, whereas stored links lead
with source URL. No schema, compiler rewrite, or sort-order change was made.

Exported all 141,612 current link rows (2,100 visits) once and wrote two local
Parquet files with identical columns and 16,384-row groups: current order
`source_url, target_url, observed_at, occurrence_id` and candidate order
`visit_id, element_index`. The current corpus fits a single observed month;
this experiment isolates ordering, not multi-month partition pruning or remote I/O.
The files were 6,990,818 and 9,622,669 bytes respectively.

A paired warm-cache comparison used one DuckDB thread, 20 deterministic random
samples per workload (Python random seed 42 over sorted distinct identities),
one warm-up and five measured repetitions, alternating order each repetition.
Queries returned `count(*), sum(length(target_url)), sum(element_index)`;
results matched for both layouts. Medians in milliseconds:

| Predicate | Current source order | Candidate visit order |
| --- | ---: | ---: |
| One capture | 0.608 | 0.693 |
| Ten captures | 3.817 | 5.823 |
| One source URL | 0.574 | 0.688 |

A separate full-row extraction comparison also checked equal returned rows.
These small local measurements do not justify a rewrite or establish billion-row
performance. Retain the current layout. Revisit with larger, multi-month data,
remote file/row-group pruning evidence, and representative capture joins before
changing it. Millisecond differences should not be interpreted as production SLAs.

Structure-first discovery (for example, finding salary tables or phone-number
form controls before identifying their pages) remains valid public SQL. Content
hash partitioning and content/node ordering optimize document-local extraction;
they do not promise pruning for text, tag, or attribute predicates across the
corpus. Measure a recurring reverse-discovery workload before adding a feature
projection or changing the common primitive ordering.

A structure-first diagnostic selected distinct content IDs from
`html_form_control WHERE tag = 'input' AND lower(type) = 'tel' AND required`,
then joined captures. It reached the public 20-second deadline during a concurrent
rebuild. The corresponding primitive attribute search finished in 2.127 seconds
and returned no rows. EXPLAIN retained the form view's owner-resolution joins and
window despite ownership not being selected (12 join nodes versus 3 in the
primitive query, including capture expansion). This is evidence of avoidable
view/optimizer work, not evidence that hash partitioning must change. Timing was
not isolated; investigate projection/join elimination before considering stored
form projections. No rewrite or physical change was made for this diagnostic.
A repeat of the primitive query with the explicit HTML namespace predicate took
14.811 seconds under rebuild load, again returning no rows. This reinforces that
the timings are contention-sensitive; the retained unnecessary owner joins in
EXPLAIN, rather than the timing ratio, motivate the optimizer investigation.

The physical cleanup rebuild additionally exposed per-identity retention-fence
round trips: a worker stack sample was inside `retention.identities.touch` during
commit. This is writer execution overhead, not a reason to alter the public
schema or node/link ordering. Fence reads and writes now use set-based batches of
the same identities, preserving validation order, revision increments, and the
same transactional write conflicts. Tests cover mixed creation/update, duplicates,
missing/retired identities, and existing retirement races. The local rebuild was
restarted with 50 visits per batch after the 500-visit run encountered high memory
use and a worker restart; no default batch-size or physical partition change was
made. Shared-host load and swap also affected the rebuild, so its elapsed time is
not an isolated layout benchmark.

### Depth rebuild performance investigation (2026-09-08)

Classification: observed writer/coordination overhead and host contention; no
public-schema or HTML node sort-order defect established. Run
edb7f64a-c96d-47b2-b8e9-23d85c076793 uses 213 ten-visit batches. This smaller
operator-selected batch size reduces peak memory but multiplies fixed work.

An early scrape across four materializers covered 55 committed batches: mean
recorded preparation was 10.4 seconds, Parquet writing 5.9 seconds, and successful
commit 6.8 seconds. Workers reported 167 transaction conflicts and 17 outer retries.
These are cumulative concurrent-worker measurements, not additive wall time. The
phase metrics exclude connection setup, projection-to-Arrow conversion (currently
between timers), failed commit attempts, and backoff. Preparation includes source
SQL and raw-object reads, not just parsing.

A read-only probe over ten documents (374,378 source bytes) measured catalogue
connection setup at 7.34 seconds, visit selection at 5.99 seconds cold / 3.39 warm,
and source resolution at 34.44 seconds cold / 5.59 warm. Source resolution on this
already-committed batch skips new-content ownership lookup, so it does not exactly
reproduce an uncommitted batch. Measurements were under concurrent rebuild/build
load: host load average around 38 and swap usage about 4.5 GB.

A short worker profile frequently sampled retention identity SELECT/UPDATE, plus
source selection and Parquet writes. Sampling fell behind schedule under load, so
it supports locations, not precise CPU percentages. The active guard table had
16 data files (325,940 bytes) and 31 delete files (57,123 bytes); visits had two
files, documents 48, and the old active HTML node/element tables eight each. These
counts do not by themselves prove a file-layout problem.

A bounded one-replica experiment retained the normal two writer lanes. Over about
121 seconds it progressed from 62 to 70 completed batches and the surviving
worker's conflict counter rose from 46 to 51. Four replicas were restored. Mixed
batch sizes in bytes, changing host load, and other writers prevent treating this
as a controlled throughput comparison or evidence that one replica is optimal.

Next investigations should account for complete batch wall time, classify actual
transaction conflict reasons, measure reusable per-lane connection/cache benefits,
and balance batch bytes against per-file registration overhead. Each batch opens
a fresh catalogue connection, emits one file per populated projection partition,
and registers files one at a time inside the transaction. Do not change public
relations or node ordering to conceal these writer costs.

Later metadata-Postgres logs identified concrete concurrent-commit collisions:
`ducklake_snapshot_pkey` violations for snapshot IDs 8162, 8163, 8164, 8168, and
8170 around 12:50–12:51 UTC. These are DuckLake snapshot allocation conflicts,
not duplicate Periplus retention identities. They establish that not all retry
cost can be attributed specifically to guards. Moving operational state out does
not eliminate native lake commit contention; batching and writer concurrency
still matter. The frequency alone does not establish an upstream defect.

### Operational-state cutover measurements (2026-09-08)

Classification: catalogue/storage write design, not a public SQL optimizer issue.
The four Periplus bookkeeping data tables were removed after their durable state
was transferred to control Postgres. Generation commits now use exact Postgres
claims and deterministic derived-identity replacement; there is no SQL rewrite
intended to hide the previous bookkeeping cost.

Replacement rebuild `31cf5645-c380-472a-8e1d-9d5d4ab3edf4` uses 50-visit batches.
It reached 40/43 batches (1,973 visits) in roughly three minutes. At a later sample,
the surviving worker metrics covered 36 completed batches: zero transaction
conflicts, 147.247 seconds total commit phase, averaging 4.09 seconds per batch,
including claim acquisition/waiting. These are not a controlled comparison with
the earlier 10-visit sample (55 completions, 167 conflicts, 6.8-second mean
successful commit): batch composition, host load and worker lifetimes differ.

The final two large batches required retries and substantial preparation time.
NATS logged 5–11-second control-request delays around 13:23:53 UTC; ingestor
observation tasks subsequently timed out and the processes restarted. A materializer
also restarted. Do not attribute all elapsed-time differences to the Postgres move,
or infer that removing lake bookkeeping solves parser memory and batch-size costs.


The replacement run completed and activated in 632.73 seconds (10m33s), with
44 batches including catch-up, 2,124 visits and 8,728,033 rows. This is a modest
wall-clock improvement over the earlier roughly 12-minute 50-visit run, not a
controlled speedup claim. The largest batches dominate the tail despite the
reduction in commit contention.

The new commit protocol performs identity-scoped replacement of derived rows on
each batch, then records its receipt in Postgres. That is a deliberate correctness
trade-off for safe replay across the two stores. This small corpus does not prove
its billion-row cost: content predicates match the HTML sort key, while visit-ID
replacement on link occurrences still deserves partition-pruning measurements
before freezing a large-scale physical layout. Public relations are unchanged.

### Projection allocation optimization (2026-09-08)

Classification: materialization runtime allocation/lifetime overhead, not a
public schema, partitioning or SQL optimizer defect. HTML projections now feed
Arrow in 8,192-row chunks instead of constructing whole-batch Python row lists.
Flat node fields are copied explicitly instead of recursively applying
`dataclasses.astuple`; element attribute dictionaries are passed directly to
Arrow rather than duplicated into lists of pairs. Preparation writes and releases
one projection's Arrow table at a time. DOM traversal skips redundant leaf
boundary replacement and unlinks finished parser nodes after copying their
values; children are already unlinked, keeping cleanup shallow. HTML5 parsing,
row identities, schema, partitioning and sort order are unchanged.

Read-only replays of batches 21 and 22 from run
`31cf5645-c380-472a-8e1d-9d5d4ab3edf4`, snapshot 8246, used the same local
2-CPU/4-GiB container bounds, 2 DuckDB threads and 2-GB DuckDB memory limit.
Parquet was written to temporary local files and never registered or uploaded.
The baseline retained all projection outputs; the revised replay used the new
one-projection-at-a-time preparation order. RSS was sampled every 100 ms.

| Measurement | Batch 21 before → after | Batch 22 before → after |
| --- | ---: | ---: |
| All projection row/Arrow construction | 3.86 → 1.64 s | 8.12 → 2.79 s |
| Node projection row/Arrow construction | 1.96 → 0.42 s | 2.92 → 0.72 s |
| Total preparation replay, including local Parquet | 27.15 → 22.12 s | 46.97 → 32.78 s |
| Sampled peak process RSS | 1.64 → 1.22 GiB | 2.13 → 1.45 GiB |
| Output rows (unchanged) | 982,959 | 1,549,516 |

These are single local replays with warm infrastructure, not production sizing
recommendations, repeated-trial medians, or end-to-end speedups. Uploads, lake
commit/claim waits and concurrent rebuild contention are excluded. The registry
implementation digest changes, so deployment requires the normal complete
materialization rebuild; no public schema version change is needed.

Validation compared all 50 source documents in batch 22 against the pre-change
implementation: every node field, element record and node/element Arrow table
matched exactly. Unit fixtures cover chunk boundaries, empty typed outputs,
attribute maps, nulls and leaf subtree boundaries.

### Single-construction DOM records (2026-09-08)

A follow-up runtime optimization reserves preorder positions when entering a
node and constructs each final immutable node/element record when leaving it.
This removes the remaining `dataclasses.replace` calls and the temporary element
lookup dictionary. Completed records retain the same order and complete shared
parse representation for every projection. Parent direct text is copied before
unlinking the parent; already-unlinked text children retain their data.

Paired replays used the same snapshot, batches and container bounds as above,
with the immediately preceding allocation optimization as the baseline. Order
was after/before for batch 21 and before/after for batch 22. Each variant ran
alone, used temporary local Parquet and did not upload or commit data.

| Measurement | Batch 21 before → after | Batch 22 before → after |
| --- | ---: | ---: |
| DOM traversal/record construction, excluding parsing and normalization | 4.41 → 2.68 s | 5.82 → 4.09 s |
| Total preparation replay | 23.13 → 20.11 s | 31.37 → 29.85 s |
| Sampled peak RSS | 1.20 → 1.22 GiB | 1.44 → 1.42 GiB |

Record construction took 30–39% less time; observed total preparation took
5–13% less time. Peak memory was essentially unchanged. These are single paired
local measurements, not production throughput or repeated-trial estimates.

Exact comparison against the preceding implementation passed for all 50 source
documents in batch 22: every node field, element record and both HTML projection
Arrow tables matched. A nested mixed-content fixture additionally checks preorder,
subtree boundaries, attributes and parent direct text after child cleanup.

### Prose deployment validation (2026-09-09)

The prose rebuild exposed a parser-disposal defect on captured HTML with both
`lang` and `xml:lang` on the root. html5lib's minidom tree can retain both qualified
attributes while sharing a local-name lookup key. Element cleanup then attempted
to delete that key twice. After copying immutable records, parsing now detaches
attribute owners before disposing the complete element. A minimal regression
fixture and the actual failing capture both parse successfully. This is parser
cleanup, not a change to public text or attribute semantics.

The worked comparison under `docs/examples/prose/` searches captured book product
pages for `robot`, then extracts titles and URLs. Initial correlated ancestor
exclusion exhausted public memory/spill limits. The final baseline uses ordered
subtree-end windows and explicit source scope instead. These are hand-written
public SQL examples, not an installed compiler rewrite; the prose materialization
removes reconstruction work directly. See the example README for validation.

### Requested-key propagation investigation (2026-09-09)

[The investigation](query-investigations/key-domain/README.md) classifies selective
joins to grouped/windowed derived relations as an optimizer problem and records
actual plans, corpus scale, nine differential cases, warm paired measurements,
and a standalone non-HTML reproduction. Propagating complete selected key domains
into inputs reduces aggregation/window work, but elapsed-time gains and file
pruning are not universal. Prep requires bound lineage, partition-locality proofs
and conservative plan selection before this can become a general automatic pass.
That investigation did not activate a runtime rewrite. The reviewed, deliberately narrower
implementation below follows the subsequent scope decision; materializations remain unchanged.


### Retired text and content-scoping experiments

Previous postings, vocabulary, prose and content-scoping investigations are historical
measurements. Their implementations are removed by the canonical element release.
See the individual reports under query-investigations for original evidence.

