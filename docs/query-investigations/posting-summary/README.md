# Content summaries versus positional postings

Status: experiments completed on 2026-09-12; no production layout or public contract changes.
Classification: schema/layout, with optimizer plans inspected as a contributing factor.
Hypothesis: page summaries reduce discovery/ranking work versus occurrence rows, but
packing positions at term/content grain may retain that advantage without a separate table.

Use the shared query benchmark, fixed results, two threads and 4 GiB, both variant
orders. Production comparison is read-only at one snapshot. Compare discovery only
there: current node frequencies are not additive when a token crosses nodes.
An isolated retained HTML sample supplies real ICU tokens for positional layouts.
Controlled synthetic growth separates growing matches from unrelated appended data.
All local Parquet is disposable, ZSTD compressed, term-major sorted per append.
No timing is described as cold-cache or as proof of billion-capture scalability.

## Protocol and semantics

The public case is a binding smoke case. Supporting scripts intentionally query
internal layouts, not a proposed public posting surface:

- `benchmarks/query/experiments/posting_summary_live.py`: read-only production
  discovery, content summary versus current node counts, both variant orders.
- `benchmarks/query/experiments/posting_summary.py`: disposable local Parquet
  layouts measured through `_measure`, with an empty disposable DuckLake attachment
  for the shared benchmark snapshot metadata. It does not emulate remote S3 or
  DuckLake file selection and is not production query-service latency.

The five variants are:

| Variant | Grain / fields read for frequency aggregation |
| --- | --- |
| summary | term/content; scalar frequency |
| flat | term/content/token position; count occurrences |
| packed_node | term/content/node; unnest positions and count distinct token ordinals |
| packed | term/content; length of positions list |
| packed_frequency | same packed file; read its scalar frequency instead of the positions |

Flat occurrences carry a list of contributing node IDs. Packed content rows carry
parallel positions and node-ID lists. Positions are document-global ICU token
ordinals; a token split across nodes remains one occurrence with multiple owners.
The research tokenizer is checked against both production content and node counters
on every retained HTML input. The unit fixture explicitly demonstrates why summing
current node frequencies overcounts split words.

Discovery requires all distinct requested terms. The other workload aggregates exact token
frequencies by page, which is input to ranking; it does not measure a production
ranking formula, phrase search, snippets, top-k optimization or API overhead.
Every variant computes a full-set count and XOR hash, plus total frequency for the
frequency workload. Shared result digests and types must agree in both orders.
These are bounded fingerprints, not a formal proof; adversarial fixtures compare
actual aggregate rows and production tokenizer counts as well.

All layouts use ZSTD and 32,768-row groups, sorted by term/content within each
append. The real sample is the first 500 retained HTML files in hash order (not a
random sample). Growth is explicitly synthetic: 200 tokens per page, two common
terms, roughly 10,000 noise terms, SHA-256-shaped content identities, and ten fixed
rare-term pages. Common-result sets grow; rare-result keys remain fixed. Synthetic
nodes own groups of ten tokens. This tests index behavior, not realistic web-domain
or language distributions.

Two growth configurations distinguish a rare term outside the noise term range
(`--rare-id 9999999`) from one inside overlapping file ranges (`--rare-id 5003`).
Noise generation excludes the rare ID. Appends add 10,000 pages per file per layout.
The final compact variant rewrites the same corpus globally sorted into one file
per layout. This is a local physical-layout experiment, not LakeDucktor maintenance.

Warm profiles follow an ordinary execution; variants run forward and reverse.
No cold-cache claim. Cached `total_bytes_read=0` does not imply zero scan work.
Buffer memory fields are connection high-water marks, contaminated by preparation
and prior queries; they are not isolated per-layout RSS comparisons.

## Production evidence

Read-only in-cluster materializer connection at snapshot **346888**, DuckDB 1.5.5,
DuckLake d8a1881e, two threads, 4 GiB. No registered files, tables, registry entries,
service settings or rebuild state were changed. Current content postings are
term-major; node postings are content-major, so the following comparison includes
both grain and sort-order differences.

| Exact-term discovery | Content summary warm ms, forward / reverse | Node postings warm ms, forward / reverse |
| --- | ---: | ---: |
| microcontroller | 19.4 / 20.5 | 33.5 / 27.8 |
| robot | 35.2 / 30.3 | 204.8 / 163.9 |
| the | 47.8 / 60.1 | 563.6 / 546.9 |
| robot AND the | 70.1 / 54.3 | 697.3 / 675.9 |

All paired full-set fingerprints matched. First executions had substantial cache
bias: for example node lookup for `the` took 8.18 s initially and 0.53 s on the
reverse ordinary run. Do not advertise that initial difference as a stable ratio.

`robot` emitted 828 content rows versus 1,888 node rows. `the` emitted 125,539
content rows versus 3,120,564 node rows. The two-key optional filter emitted
2,251,492 rows from the content scan versus 224,609,425 from the node scan before
later filtering. Those are profiler operator outputs, not useful result counts.
The common/robot cases still opened all 13 content files or 14 node files; the rare
case opened seven files on either layout. Content summaries help today, but this
is not a bounded-file-read result.

## Initial local evidence

The 500-page sample contains **705,774 tokens** and **41,451 vocabulary terms**.
Compressed storage: summary 520,157 B; flat positions 3,918,646 B; packed-node
positions 3,753,606 B; packed-content positions plus frequency 3,101,974 B.
Adding a separate summary alongside the packed-content representation costs about
17% extra in this sample. These are sample-specific sizes, not corpus projections.

At 100,000 synthetic pages / 20 million token occurrences in ten appended files,
median warm frequency-aggregation timings across the two orders were:

| Workload | Summary | Flat positions | Packed per node | Packed per page, list length | Packed per page, frequency |
| --- | ---: | ---: | ---: | ---: | ---: |
| common term | 13.3 ms | 32.1 ms | 161.3 ms | 19.9 ms | 13.8 ms |
| two common terms | 16.1 ms | 68.4 ms | 318.2 ms | 25.6 ms | 14.8 ms |

This first run used an out-of-range rare ID. Its local scan summaries were missing:
the shared profile walker recognized names containing `SCAN`, but DuckDB names the
Parquet operator `READ_PARQUET`. It now also recognizes `operator_type=TABLE_SCAN`,
with a regression test. The initial timings and fingerprints remain valid; do not
infer missing file counts as zero. The overlapping-range run uses the corrected
instrumentation.

## Reproduction

From `packages/periplus`, after syncing the project environment:

```sh
uv run python ../../benchmarks/query/experiments/posting_summary.py \
  --input-dir /absolute/path/to/retained-html \
  --documents 500 --growth-documents 300000 --rare-id 5003 \
  --report ../../.artifacts/query-benchmarks/posting-summary-overlap.jsonl
```

The first 100,000-page run used `--growth-documents 100000 --rare-id 9999999`.
The script removes its temporary lake and Parquet on completion. JSONL reports stay
under ignored `.artifacts/`; commit only these sanitized findings. Local generation
is bounded to one million synthetic pages and 1,000 retained HTML inputs. Each
query receives the shared 120-second deadline and bounded result collection.

For an explicitly authorized production comparison, send the read-only live
script to `python -u -` in an existing materializer through the homelab
`make run CMD="kubectl ... exec -i ..."` wrapper. Do not invoke it during rebuild
activation or concurrent heavy reader probes. Its lake transaction always rolls
back. No public catalogue or materialization installation is part of this experiment.

## Completed overlapping-range growth test

Completed **360 paired-order measurements** across the real sample, 10k / 30k /
100k / 300k synthetic pages, and the final compacted 300k corpus. Every variant
fingerprint matched. The fixed rare-term fingerprint also matched across every
scale and after compaction. The final corpus has **60 million token occurrences**;
300k synthetic short pages are not equivalent to 300k real web pages in byte or
posting volume. This is a scaling curve, not a billion-capture capacity proof.

Median warm times across the forward/reverse profiles at 300k pages:

| Workload / layout | Summary | Flat positions | Packed per node | Packed per page, list length | Packed per page, frequency |
| --- | ---: | ---: | ---: | ---: | ---: |
| Rare discovery, 30 appends | 7.94 ms | 10.25 ms | 10.06 ms | 9.37 ms | 10.02 ms |
| Common frequency, 30 appends | 38.53 ms | 94.84 ms | 506.72 ms | 55.74 ms | 39.33 ms |
| Two-term frequency, 30 appends | 42.32 ms | 186.94 ms | 1089.84 ms | 75.15 ms | 43.45 ms |
| Rare discovery, one compact file | 12.46 ms | 18.22 ms | 17.82 ms | 16.39 ms | 16.46 ms |
| Common frequency, one compact file | 38.48 ms | 98.36 ms | 520.43 ms | 61.54 ms | 44.07 ms |
| Two-term frequency, one compact file | 57.10 ms | 178.86 ms | 1085.16 ms | 95.03 ms | 62.48 ms |

For a common term, summary/packed-content scans emit 300k rows; flat/packed-node
scans emit 5,999,990. For two common terms those figures are 600k and 11,999,990.
Every appended layout opens all 30 files; every compacted layout opens one.
Rare lookup still emits just ten rows: fixed matches do not keep file count fixed
under appends with overlapping term ranges. Native profiler `rows_scanned` counters
are not a count of decoded matching rows or an exact row-group read measurement.
Warm byte counters are zero from caching; this experiment establishes neither cold
S3 bytes nor row-group-level I/O. No query spill was reported; memory high-water
marks cannot isolate layout-specific query memory because the connection built the
fixtures and executed earlier queries.

The two common synthetic term IDs are adjacent, permitting a cheap range filter;
this does not resolve the existing native multi-key filtering issue for widely
separated IDs. The real-sample and live `robot AND the` probes cover nonadjacent
IDs, but a larger controlled nonadjacent-key study remains a distinct limitation.
The packed-node strategy uses unnest/distinct to preserve split-word correctness;
it is a measured implementation, not proof that every node-grain strategy must
pay this exact cost.

At 300k pages the 30 appended files occupy 676,787,641 B for summaries and
754,850,777 B for packed-content positions plus frequency. The one-file rewrite
increases these to 1,667,867,176 B and 1,745,891,715 B respectively. Sorting and
Parquet encoding interactions therefore need measurement: fewer files did not mean
less storage or faster rare lookup here. Do not turn this experiment into a
production compaction recommendation.

## Decision

1. Keep the current content postings while implementing the existing-index search
   path. They provide measured benefits over the current node posting access path.
2. If introducing positions, prototype **one row per term/content with scalar
   frequency, positions, and node provenance** as the principal alternative.
   Its scalar projection retains the useful summary in the same table. A second
   content-summary table showed little additional benefit in appended local tests;
   the compact-file tests showed a smaller but nonzero difference. This does not
   yet prove equivalence through a production DuckLake deployment.
3. Do not replace content postings with flat occurrences based on storage
   minimalism. The frequency-aggregation scan is materially more expensive.
4. Do not rebuild or change the registry from these experiments. Remaining release
   work includes positional phrase/provenance retrieval, realistic large-document
   distributions, selective node/snippet extraction, nonadjacent term sets under
   controlled growth, object-store cold I/O, and a measured append/maintenance
   policy. Those are separate from proving that the summary grain helps.

The only shared-code change is benchmark instrumentation for Parquet table scans.
No query API, projection, schema, crawler setting, deployment or upstream fix was
introduced. The first overlapping run was stopped to apply that reporting fix and
rerun; only its replacement completed artifact is used above. Disposable local
files were removed on completion. The retained HTML input was left unchanged.

## Validation

`make check` passed after the benchmark change: 791 backend tests (34 environment
skips), 24 SDK tests (6 skips), and the package/frontend checks and builds. Targeted
fixtures cover split-token provenance, Unicode composition, equal discovery and
frequency totals for all five layouts, empty matches, and Parquet scan reporting.
Experiment scripts pass Ruff. No production deployment or rebuild was performed.
