# Mapping and deduplication follow-up

**Result (2026-09-13): direct per-element token-range mapping reduced mapping,
deduplication and option-2 packing from 21.10–21.24 seconds to 2.630–2.631 seconds
on the same 500 contents, about 8× faster.** Complete outputs remained identical.
This is a local experimental implementation, not a deployed projection or rebuild.

Classification: materialization construction algorithm, within the existing
schema/catalogue investigation. No public schema or query compiler change.
The baseline plan uses a content equality hash join with interval predicates,
then DISTINCT, then packs option-2 arrays. Hypothesis: avoiding that intermediate
occurrence/ancestor expansion reduces construction work while retaining exact
term/content/node matches.

Candidates: explicit batch filtering in SQL; then batch-local integer content keys;
and direct token-range lookup per element using binary search on already ordered
page-token starts/ends, a per-element distinct term set, and direct node-list
construction. No persistent vocabulary allocator or mutable index state.

The existing 500-content snapshot and ICU tokenizer are unchanged. Each variant
must produce exactly the reference option-2 rows, including sorted node arrays,
using bidirectional EXCEPT ALL for every 50-content batch outside measured time.
Read, tokenizer, Arrow, mapping/deduplication and packing costs are separated.
The output endpoint is an in-memory DuckDB option-2 table for every variant;
it excludes Parquet encoding, lake publication and production coordination.
Results are compared in forward and reverse order in separate processes, with
2 DuckDB threads and 2 GB engine memory. No simultaneous performance runs.

Reproduce from `packages/periplus`:

```sh
uv run --with PyICU==2.16.2 python ../../benchmarks/query/experiments/index-layout/map_probe.py \
  --source ../../.artifacts/index-layout/icu-source \
  --output ../../.artifacts/index-layout/map-baseline-0 --mode baseline
```

Use a new output directory for each run, replacing `baseline` with `scoped`,
`integer` or `bisect`. Run baseline/scoped/integer/bisect followed by the reverse
order. First-batch SQL profiles are captured separately from wall timings.
Original-span correctness is also checked against brute-force containment on
randomized element boundaries, including repeated words, astral characters,
combining marks, empty ranges and case-fold expansion.

## Why the direct mapping is equivalent

ICU emits ordered, non-overlapping word spans in the original page. For an element,
find the first word starting at or after its start and the last word ending at or
before its end. Only those complete words belong to the element. Taking a set of
their normalized terms removes repetitions before recording that element's node
index once per term. Finally sort each node list. Equal element ranges, enclosing
ancestors and words crossing inline element boundaries need no special cases.

The implementation uses two standard-library binary searches and a Python set;
it does not tokenize elements again. It still examines words contained in ancestors,
so very deep markup and exceptionally large pages can cost more. This is a measured
simple improvement, not a claim of constant work per page or a new tree-index design.

## Measured comparison

Two serial passes, the second in reverse variant order; seconds summed across ten
50-content batches. These are paired observations, not statistical confidence bounds.

| Method | Map/dedupe/pack pass 1 | Pass 2 | Read + ICU + map/dedupe/pack, pass 1 / 2 |
|---|---:|---:|---:|
| Original SQL join + DISTINCT + grouping | 21.098 | 21.239 | 27.653 / 28.168 |
| Same SQL, explicitly restrict elements to current batch | 21.429 | 21.180 | 28.252 / 27.786 |
| Restricted SQL with integer content keys | 20.917 | 21.085 | 27.553 / 27.698 |
| Direct token ranges, local distinct terms, build arrays | **2.631** | **2.630** | **9.399 / 9.451** |

The small SQL changes did not remove the main cost. In the baseline's first-batch
profile, the hash join emits 3,482,115 occurrence/element matches and DISTINCT
reduces those to 773,814 matches. The join's input element scan emits 593,844 rows
from the 594,873-element reference table, despite the batch containing only 50
contents. Explicit batch restriction helps scan scope but does not materially
improve total time here; replacing content hashes with integers also has little
effect. The direct mapper avoids that join and its expanded intermediate relation.

In the forward run, the old mapping stage spent 1.252 seconds constructing Arrow
occurrences, 19.399 seconds joining/deduplicating, and 0.447 seconds packing arrays.
The new path spent 2.233 seconds mapping/deduplicating/building lists and 0.398
seconds converting those lists to Arrow and materializing the common output table.
ICU itself remained approximately 5.9 seconds. The slowest mapping batch dropped
from 6.014 to 0.327 seconds in that run.

All eight measured executions yielded exactly **849,151 option-2 rows containing
7,317,496 distinct element matches**. Full per-batch multiset checks compared actual
term strings, content identities and complete sorted node arrays against the retained
reference; no count-only correctness shortcut was used. Six semantic tests passed,
including 100 deterministic randomized boundary cases against brute-force containment.
`make check` passed after the experiment, including backend/SDK tests and
package/public/admin checks and builds. Experiment scripts also compile successfully.

Total process peak RSS was 1.43–1.49 GB for baseline and 1.10–1.12 GB for direct
mapping. These include fixture reads, verification, and SQL profiling where applicable;
they are not isolated mapper memory or production worker sizing measurements.

The input is already-parsed canonical page text and element offsets. Approximately
three times faster **preparation** here does not establish three times faster full
materialization: HTML read/parse, Parquet encoding/upload, lake commits and production
coordination are outside this experiment. The earlier report's 22.23-second mapping
figure had a different output endpoint (persisted flat reference matches); the table
above reruns every candidate with the same option-2 endpoint instead of mixing timings.

Preliminary run-0 measurements were excluded from the paired table; two exploratory
processes briefly overlapped. All reported pass-1/pass-2 performance runs were serial,
and repository checks ran afterward. Raw reports/profiles are in ignored
`.artifacts/index-layout/map-{baseline,scoped,integer,bisect}-{1,2}/`.

## Decision

Use the direct mapper as the candidate when implementing option 2. It is a small,
batch-local algorithm using existing offsets, plain Python sets and binary search;
it requires no application-aware maintenance, persistent dictionary or updates to
existing node arrays. ICU still runs once on page text; title remains in that text
and metadata attributes remain excluded. Production integration and throughput
verification remain separate work before rebuilding the corpus.
