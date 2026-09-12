# Fixed multi-content extraction under unrelated appends

Historical investigation: the public prose/term surfaces and associated compiler paths
were removed by the search contract release. Commands below describe the earlier revision;
the old query cases now live under `benchmarks/query/retired/`.

Status: diagnosis complete; physical-read acceptance still open, 2026-09-11.
No production mutation, runtime setting change or deployment.
Classification: native scan filtering and physical layout/maintenance, following
[the API barrier experiment](query-api-barrier.md). Public semantics are unchanged.

Hypothesis: input scoping keeps heading reconstruction proportional to the selected
contents, but multi-key scan output and files read grow with unrelated corpus data.
Compare 10, 100 and 1,000 fixed matching contents at 2,000, 5,000 and 10,000 total
contents, before and after compaction. Keep selected HTML bytes and result digests
fixed across every growth stage. All comparisons use the shared query bench in
both orders within a read transaction; complete rows/types/order must agree.

The existing disposable runner adds fixed marker terms to deterministic synthetic
HTML. Each content has 14 headings including an empty heading. The public prose
and experimental term predicates select the same exact keys in this fixture.
No billion-scale or production-read claim follows from these local measurements.

```sh
cd packages/periplus
uv run python ../../benchmarks/query/experiments/term_extraction.py \
  --scales 2000 5000 10000 --batch-size 500 --match-counts 10 100 1000 \
  --report ../../.artifacts/query-benchmarks/term-multikey.json
```

The original one/two-key diagnostic remains separately runnable. Multi-key runs
omit UNION ALL per-key branches: generating thousands of full extraction branches
is not a scalable proposed API solution. Native IN, VALUES keys, term joins and
input scoping remain in the comparison. Production node/element projectors and
view definitions, eight content-hash buckets, two threads and 512MB requested
memory follow the preceding experiment. Each query retains the 60-second deadline.

## Additional controls

Two additional isolated runs test finer row groups and the native set-filter
cutoff without changing production configuration:

```sh
uv run python ../../benchmarks/query/experiments/term_extraction.py \
  --scales 2000 5000 10000 --batch-size 500 --match-counts 10 100 1000 \
  --dom-row-group-size 8192 \
  --report ../../.artifacts/query-benchmarks/term-multikey-small-groups.json
uv run python ../../benchmarks/query/experiments/term_extraction.py \
  --scales 10000 --batch-size 500 --match-counts 10 100 1000 \
  --dom-row-group-size 8192 --probe-set-filter \
  --report ../../.artifacts/query-benchmarks/term-multikey-set-filter.json
```

Only the DOM row-group setting changes in the smaller-group run; postings and
prose retain their previous layouts. Compaction remains the same native local
merge operation on postings, prose, nodes and elements. Later appends start from
the preceding compacted state. No vocabulary compaction is introduced.

The set-filter probe runs on one final compacted snapshot, with
`dynamic_or_filter_threshold` set to 50, 1024, 1024, then 50. Each setting measures
both the API prose candidate and research term candidate in both orders; all
results must match the original literal baseline. The original setting is restored
in a finally block. The engine documents the setting as the maximum dynamically
generated OR filters from a hash join; its observed default is 50.

DuckDB v1.5.5 / DuckLake `d8a1881e`, ICU 77.1 / PyICU 2.16.2. These are local
filesystem warm profiles with two threads and 488.2 MiB effective memory, not cold
object-store or deployed API latency. Measurements finished before the full
project checks. A small sortedness probe overlapped the earlier part of the
small-group run; the final 10,000-content comparisons were not concurrent.

## Result: reconstruction is bounded, scans are not

Both layout runs preserve the exact selected keys and complete ordered results
at every stage. All three reports completed successfully. At all corpus sizes,
the API candidate reconstructs exactly 140, 1,400 and 14,000 heading groups for
10, 100 and 1,000 matched contents. Native queries reconstruct 28,000, 70,000 and
140,000 groups as unrelated corpus size grows from 2,000 to 10,000 contents.
Final result digests, columns, types and row counts also agree across all three
fixtures for each of the six scope/relation families, not just within each run.

Default row groups, compacted state, actual API compiler candidate:

| Fixed matches | Candidate ms at 2,000 total | Candidate ms at 10,000 total | Native ms at 10,000 total | Candidate node scan output at 10,000 |
| --- | --- | --- | --- | --- |
| 10 | 10.8–11.0 | 17.0–17.5 | 272–281 | 560,562 |
| 100 | 14.0–14.5 | 22.6–23.1 | 274–275 | 1,060,030 |
| 1,000 | 37.9–39.5 | 49.6–51.4 | 259–264 | 1,079,056 |

The required full-content node counts are only 1,090, 10,900 and 109,000. The
same-snapshot term-barrier results agree and show similar timings. Prose extraction
also remains broad: term membership emits all 10,000 prose rows from its scan
before returning 10, 100 or 1,000 matches. Literal IN likewise emits all prose
rows. Materializing the term vocabulary is not the cause of this downstream cost.

At 10 matches, the compacted candidate opens five of eight DOM files. After ten
unrelated 500-content batches, it opens 55 of 88 files per DOM table, although
only five contain any requested key. At 100/1,000 matches it opens all 88 files;
compaction reduces that to eight, but the amount of unrelated scan output stays
large. A small file count is therefore not a bounded-read result.

## Smaller row groups: insufficient benefit to change defaults

At 10,000 compacted contents:

| Fixed matches | Default node scan output | 8,192-row group output | Default API ms | Smaller-group API ms |
| --- | --- | --- | --- | --- |
| 10 | 560,562 | 342,433 | 17.0–17.5 | 18.2–18.6 |
| 100 | 1,060,030 | 1,060,030 | 22.6–23.1 | 27.9–28.3 |
| 1,000 | 1,079,056 | 1,079,056 | 49.6–51.4 | 53.5–55.0 |

Finer row groups reduce some unnecessary output for ten keys, but do not improve
the larger sets. Active DOM Parquet bytes rise from 10,903,573 to 12,023,174
(approximately 10%). These are separate controlled layout runs, not same-snapshot
latency pairs; the physical counts are the stronger evidence. A separate
2,000-content check found all eight compacted node files ordered by content hash,
so that probe does not support blaming a complete absence of clustering.

Scan output is not bytes decoded or physical rows inspected. The runner's
Parquet range-candidate estimates omit bucket pruning and count all columns;
they must not be presented as measured I/O. No cold-read or network budget is
established. Compaction reduces file fan-out but does not always reduce active
Parquet bytes for this fixture.

## Higher set-filter cutoff: rejected

At the default cutoff, ten-key scans carry optional IN and min/max filters.
The 100- and 1,000-key scans carry only a broad min/max range over content hashes.
Raising the cutoff to 1,024 adds optional IN for those larger sets, but neither
their node/element scan output nor files read improves on the same snapshot.

| Fixed matches | API ms, cutoff 50 | API ms, cutoff 1024 | Node output under either setting |
| --- | --- | --- | --- |
| 10 | 18.1–18.9 | 17.4–17.9 | 342,433 |
| 100 | 27.5–28.5 | 36.8–37.5 | 1,060,030 |
| 1,000 | 52.8–55.1 | 134.6–138.7 | 1,079,056 |

Both setting orders agree. The same counts persist for the term candidate.
This disproves the proposed simple tuning fix for this workload: carrying more
individual keys in the plan does not make this scan execute them effectively.
Do not raise the production threshold on this evidence.

## Decision

Keep the existing experimental API rewrite: it bounds the expensive reconstruction
and produces large local speedups. Do not promote smaller DOM row groups or a
higher dynamic-filter cutoff from this experiment. No additional production
rewrite or layout change is justified yet.

The next target is native exact multi-key filtering at the DuckLake/Parquet scan
and row-group selection boundary, with a minimal upstream reproduction. Compare
static IN and dynamic filters while preserving the distinction between emitted
rows and actual I/O. The API cannot promise bounded storage reads merely by
rearranging the same joins or producing a longer list of keys.

Term lifecycle integration remains gated on an explicit production read-cost
envelope; these tests have not passed that gate. No new public relation, service,
index technology or maintenance owner is introduced. LakeDucktor keeps production
compaction ownership. The new regression checks that fixed multi-content results
survive unrelated appends, including empty headings and full-content membership.

Validation: both extraction regression tests passed. `make check` passed with
745 backend tests (34 environment-dependent skips), five SDK tests, and the
package/frontend checks and builds. All benchmark and threshold-probe comparisons
completed; no runtime optimization, storage setting or deployment was changed.
