# Fixed-content extraction after term discovery

Status: diagnosis complete, production acceptance still open (2026-09-11).
Local disposable lake only; no production execution ID.
Workload: discovery followed by whole-content prose/heading extraction.
Provisional classification: both optimizer and layout/access-path. Prior evidence
shows a selective term result followed by broad DOM and prose scans; this experiment
separates literal restriction, key-relation propagation and continued append layout.

Case: `benchmarks/query/cases/exp-term-extraction`. Required semantics: complete
ordered prose/headings for exactly the fixed matching contents; headings need not
contain the term. No LIMIT. Exact values, multiplicities, columns and types must
agree for every variant in both orders at the same snapshot.
The registered baseline uses the existing public prose surface so catalogue bind
checks remain valid; the disposable runner substitutes literal/key/experimental
term variants. Regex membership is equivalent only for this controlled ASCII fixture.

Hypothesis: literal IDs prune more effectively than term-derived IDs, particularly
below heading reconstruction. If neither prunes, file-range overlap or the native
scan access path contributes. Acceptance requires approximately stable downstream
files/read work for fixed keys as unrelated contents append, not just stable results.

Controls: deterministic synthetic HTML, fixed single/two-content match sets,
production DOM projectors and public view definitions, two threads/512MB,
60-second query deadlines, shared query bench. No production data mutation.
Compare unmaintained appends, local compaction, and appends after compaction.
Synthetic growth establishes a controlled diagnostic, not production capacity.

## Reproduction and environment

From `packages/periplus`, with the ICU development prerequisites in
[term-surface.md](term-surface.md):

```sh
uv run python ../../benchmarks/query/experiments/term_extraction.py \
  --scales 100 1000 5000 --batch-size 100 \
  --report ../../.artifacts/query-benchmarks/term-extraction.json
uv run python ../../benchmarks/query/experiments/term_extraction.py \
  --scales 1000 --diagnose-join-planning \
  --report ../../.artifacts/query-benchmarks/term-extraction-join-planning.json
```

DuckDB v1.5.5, DuckLake `d8a1881e`, two threads, memory setting 488.2 MiB
(512MB requested), system-default spill limit. Filesystem Parquet and DuckDB
metadata, no remote reader or query-service latency. ICU 77.1, PyICU 2.16.2.
The experiment uses the production DOM projectors and heading/node/element SQL;
prose has the same logical grain and unpartitioned content sort, under experimental
column names. DOM uses eight content-hash buckets and production sort orders.
Fixture writes use INSERT, not production external registration or lifecycle claims.

Exactly the first content matches `uniquematch`; exactly the first two match
`monkeys`. These immutable HTML bytes and keys remain unchanged throughout.
Each content has 14 headings, including one empty heading, and the matched terms
appear in prose outside the headings. Growth is intentionally synthetic.

Each size is measured before and after local compaction of postings, prose and
both DOM tables. Later appends start from the previous compacted state. Vocabulary
is not compacted. The 100-content compaction is a no-op. The main run uses snapshots
22, 22, 49, 53, 173 and 177; same-stage variant pairs share a read transaction.
Each query has one ordinary run, one warm profile and a supplementary filter
profile, then reverse variant order. None is claimed cold. Raw private reports
remain ignored locally. Join-planning diagnostic timings overlap project checks;
use their physical plans, not their timing differences, as causal evidence.

## Results

All six stages preserve the exact one/two-content sets and result digests.
All 264 measured variant results agree within their query family (six stages,
two scope sizes, five prose variants/six heading variants, two orders).
The independent semantic fixture additionally checks zero matches and empty headings.

For **one fixed content / 14 headings**:

| Corpus and maintenance | Literal node/element files | Term-derived node/element files | Literal warm ms | Term-derived warm ms |
| --- | --- | --- | --- | --- |
| 100, compacted | 1 / 1 | 8 / 8 | 8.1 | 14.6–14.8 |
| 1,000, compacted | 1 / 1 | 8 / 8 | 9.1–9.3 | 37.2–37.4 |
| 5,000, 40 batches after prior compaction | 27 / 27 | 328 / 328 | 10.7–11.0 | 187.9–188.1 |
| 5,000, compacted | 1 / 1 | 8 / 8 | 7.7–8.4 | 137.4–142.7 |

These are scan file counts per table, not total query file counts; dictionary and
postings lookup are additional. They demonstrate two distinct failures:

1. **Restriction arrives after heading reconstruction.** At 5,000 compacted
   contents, the term-derived plan constructs 70,000 heading groups before
   selecting 14. Node and element scans emit 241,224 and 275,000 rows respectively.
   A literal ID restricts those scans to 48 and 55 rows. A VALUES key relation
   also takes the broad path, so dictionary maintenance is not the cause.
2. **Literal access still suffers append overlap.** After 40 unrelated append
   batches, the fixed content exists in one node file and one element file, yet
   exact equality opens 27 of each. Parquet metadata shows broad overlapping
   content ranges. Unpartitioned prose opens all 41 files for the same single ID,
   although only one contains it. Compaction reduces fan-out, but has not proved
   stable bytes or row-group reads as compacted files grow.

Prose isolates a further planning issue: literal equality emits one row at the
prose scan, while term-derived membership emits all 5,000 before the semijoin.
This is not exclusively a heading-aggregation issue.

Two-content tests expose optional-IN behavior as well. On the compacted corpus,
literal `IN` opens two DOM files but emits 57,129 node rows and 70,455 element
rows before filtering. A diagnostic UNION ALL of separate exact equalities
emits only 48/51 node rows and 55/55 element rows. This is evidence about native
filter execution, not a proposed requirement to generate one SQL branch per hit.

Profiler output cardinality is not bytes decoded or physical rows inspected.
Parquet range-candidate counts/bytes in the report are metadata estimates without
bucket pruning and cover all columns, not measured I/O. Cache byte metrics do
not establish a cold-read budget. Even a one-file literal result is insufficient
for the production bounded-read gate.

## Causal join-planning probes

A research-only variant selects keys once and restricts **both whole-content
inputs** before applying the actual heading SQL. It preserves empty headings and
all descendant text. With ordinary optimizer settings it reduces the 5,000-content
compacted heading query to 12.9 ms and constructs only 14 groups. However,
elements still emit 275,000 rows across eight files; nodes emit 51 from one file.
The late-aggregation work improves, but the physical-read gate still fails.

On a separate compacted 1,000-content lake, holding the snapshot and SQL fixed:

| Diagnostic | Prose scan, term membership | Scoped heading elements | Scoped heading nodes |
| --- | --- | --- | --- |
| Normal optimizer | 1,000 rows / 1 file | 55,000 rows / 8 files | 51 rows / 1 file |
| Disable only `build_side_probe_side` | 1 row / 1 file | 55 rows / 1 file | 51 rows / 1 file |
| Disable only `join_order` | 1,000 rows / 1 file | 55 rows / 1 file | 51 rows / 1 file |

All results remain equal, in both measurement orders. Disabling build/probe
selection without input scoping still leaves a broad heading-node scan. Thus
neither pass alone explains the entire path. The normal prose plan puts discovered
keys on the left of a semijoin with the large prose side on the right; no content
dynamic filter reaches prose. The scoped element plan likewise lacks that filter.
The diagnostics establish sensitivity to join direction/order and native dynamic
filter propagation, not a universal rule to disable these optimizations.

## Decision and next intervention

Keep `term(content_id, text, frequency)` and private dictionary/postings. This
experiment supplies no evidence that the public term grain is wrong. Confirmed
classification: native optimizer propagation plus physical append layout/maintenance;
no public-schema workaround is justified.

The next optimizer work should isolate demanded-key propagation through heading
aggregation and join direction, using this reproduction alongside the existing
[requested-key investigation](../key-domain/README.md). A narrowly gated query-API
intervention is only acceptable if unchanged public SQL benefits with normal
optimizer settings and differential correctness tests. `term` is not yet in the
production registry; do not widen the existing capture-heading rewrite now.

Separately, measure bucket-aware file/row-group candidate bytes for exact keys
through sustained appends and maintenance. Compaction frequency, clustering and
native point/set-filter support need an explicit operating envelope. LakeDucktor
retains production compaction ownership. No global optimizer disable, new service,
production deployment or live-lake write was made. Production acceptance remains
open until both physical bounds and unchanged query-service execution pass.

Validation: the extraction semantic test and 16 shared benchmark tests passed.
`make check` passed with 742 backend tests (34 environment-dependent skips), five
SDK tests, and the package/frontend checks and builds. The benchmark's mock
heading schema now includes its existing public `level` column. No production
schema was changed to make the experimental case bind.
