# Deduplicated vocabulary and content term statistics

Status: local feasibility evidence, not a scale claim. The private materialization lifecycle is now integrated; the public term view is registered, while rollout validation remains pending.
Classification: both, with physical layout the primary cause and native scan
filtering a secondary cost (see lookup diagnosis below). Text-first discovery
needs a term-oriented candidate path; compiler rewrites cannot manufacture one.

The proposed relations are `material.vocabulary(term, term_id)` and
`material.term_stat(term_id, content_id, frequency)`. The latter has one row per
term/content pair. No positions, embeddings, new service, or production catalogue
change is introduced by this experiment.

## Hypothesis

The existing lifecycle already serializes commits by generation. A deduplicated
dictionary can admit missing words inside that transaction, assign BIGINT IDs,
and resolve prepared term strings to IDs atomically with term-stat publication.
Existing dictionary rows are never updated. A lost receipt replays content-owned
replacement without duplicating vocabulary entries or term statistics.

At the time of this experiment the production registry had only content/visit ownership; vocabulary
does not fit either. This experiment deliberately stays outside auto-discovery.
It tests the SQL operation before adding a shared-dictionary commit contract.
Production integration must preserve parallel immutable-file preparation, exact
generation claims, retention and complete-generation activation. In particular,
resolving IDs and writing term-stat files inside the serialized transaction has a
different cost from the existing prebuilt-Parquet registration path.

## Reproduce

From `packages/periplus`:

```sh
uv run python ../../benchmarks/query/experiments/vocabulary_materialization.py \
  --contents 2000 --batch-size 500 --workers 4 \
  --report ../../.artifacts/query-benchmarks/vocabulary-2000.json
```

Only a temporary local DuckLake is written, with DuckDB metadata and local
Parquet. A mutex models existing generation serialization; it does not test
distributed Postgres fencing, remote metadata conflicts, CDC, or production
rebuild activation. Independent worker cursors prepare overlapping dictionaries
in parallel. Data inlining is disabled. Term-stat files use eight term-ID buckets
and sort by term ID/content; vocabulary sorts by term. No compaction is performed.

Tokenization now uses existing body prose extraction, ICU casefold/NFC, and ICU
root-locale word segmentation. See [term-surface.md](term-surface.md) for the
current contract, native ICU prerequisites and real-content validation. Historical
measurements below predate this tokenizer change. IDs are stable within a
generation but allocation-order-dependent across rebuilds; compare rebuilds by
term/content/frequency, not numeric IDs.

The synthetic corpus has ten fixed matching contents, 100 shared generated terms
per content, and one unique generated identifier per content. This deliberately
exercises overlapping admissions and vocabulary growth, not natural-language
relevance. Query comparison uses the shared `benchmarks/query/` runner in both
orders, at the same snapshot within each pair. The baseline regex semantics are
equivalent only for this controlled lowercase ASCII corpus. The candidate uses
private prototype tables; it is not a public query API change.

Acceptance: no duplicate terms/IDs/postings, no missing references, exact counts
against independent prose tokenization, rollback after dictionary admission,
replay equality, and logical equality across differently ordered rebuilds.
Performance remains observational: record serialized write time, staged/final
storage, query profiles and candidate cardinality; never infer bounded reads from
a selective result or an outer LIMIT.

## Remaining gates

Before shipping: representative real-content tokenization/storage measurements,
Postgres-backed concurrent workers and failure recovery, growing vocabulary cost,
term-ID join pruning across accumulated files, compaction, shared vocabulary
retention semantics, and full registry activation/rebuild/live parity. Existing
generation serialization is useful evidence, not proof that a global dictionary
will be cheap at corpus scale.

## Local results (2026-09-11)

DuckDB 1.5.5, local DuckLake metadata/files, four worker threads, 500 contents per
batch, two DuckDB threads, 512 MB memory. Correctness tests passed for concurrent
overlapping batches under serialization, repeated content, content with no tokens,
receipt-loss replay, rollback after new-term admission, subsequent recovery,
snapshot visibility, and a rebuild in reversed batch order. Prose fixtures cover
inline markup, excluded subtrees, repeated words, case folding and Unicode NFC.

| Contents | Vocabulary rows | Term-stat rows | Materialize wall time | Median serialized batch | Prepared Parquet | Lake Parquet including obsolete replay files |
| --- | --- | --- | --- | --- | --- | --- |
| 2,000 | 2,107 | 208,010 | 0.79 s | 45.6 ms | 101,522 B | 1,758,489 B |
| 20,000 | 20,107 | 2,080,010 | 7.19 s | 165.6 ms | 1,003,193 B | 14,330,999 B |

Lake byte totals include prose, vocabulary and postings, exclude metadata, and
are not a minimal-index size estimate. Generated text is unusually compressible.
Serialized time includes staging reads and all three table writes; it is not
dictionary admission alone. The admission/staging median increased from 15.8 ms
to 80.7 ms. There was no compaction. These measurements overlap repository checks
and are observational, not clean throughput benchmarks.

Both query orders produced identical columns/types/results (ten content IDs).
At 2,000 contents (snapshot 15), warm prose scans were 3.81–3.84 ms and the
postings join 4.58–5.00 ms. At 20,000 contents (snapshot 51), prose was
26.66–26.84 ms and the join 7.57–8.37 ms. The term-stat scan reported 6 files
at the smaller size and 74 at the larger size despite the same ten matching
contents. Vocabulary read two files in each run. Profile `rows_scanned` exceeds
logical table sizes; retain it as engine-reported work, not distinct rows read.
Warm byte counters were zero and do not establish zero physical I/O. Thread
scheduling can change ID allocation and layout across runs, so this is not a
controlled fixed-term-ID scaling proof.

Decision: the deduplicated dictionary is locally feasible without updates to
existing terms. There is no evidence yet that it requires another technology.
Do not promote this prototype as a scalable index: serialized work grows, and
dictionary-derived term-ID joins still read increasing numbers of postings files.
Next isolate literal-ID versus vocabulary-join access and accumulated-file layout,
then test realistic vocabulary growth and the production metadata/claim path.

Validation: `make check` passed (738 backend tests, 34 environment-dependent
skips; five SDK tests; package and frontend checks/tests/builds). The focused
two-test DuckLake experiment suite also passed. No production tables were added
or rebuilt.

## Lookup diagnosis (2026-09-11)

Reproduce from `packages/periplus`:

```sh
uv run python ../../benchmarks/query/experiments/vocabulary_lookup.py \
  --report ../../.artifacts/query-benchmarks/vocabulary-lookup.json
```

This companion experiment fixes batch admission order, omits replay, and holds
term IDs and logical matches constant while isolating three query forms and a
physical compaction. It uses the shared bench measurement/deadline machinery in
both query orders, with each stage pinned in one read transaction. It inspects
Parquet footer ranges and actual term membership outside query timings. Only the
disposable lake is compacted; production maintenance remains LakeDucktor-owned.

### Primary cause: overlapping file ranges, not a lost vocabulary predicate

Twenty thousand contents in forty batches generate 320 postings files: eight
term-ID buckets per batch. `monkeys=504` belongs to bucket 7; `zoo=607` belongs to
bucket 3. Each term is present in exactly ONE file. Nevertheless, all forty files
in each relevant bucket have min/max ranges enclosing the requested ID.

For example, later bucket-7 files span IDs 502–1087 and 502–1605. These files
contain common old terms and newly allocated high-ID document tokens, but not
term 504. Their min/max statistics cannot prove that absence. Sorting is local
to each batch file; it does not make ranges disjoint across appends. The bucket
filter works (two of eight buckets), but every batch still contributes a file
to each of those buckets.

| Form | Uncompacted files | After sorted compaction | After one unrelated 500-content append |
| --- | --- | --- | --- |
| Vocabulary join | 80 | 2 | 4 |
| Literal `term_id IN (504,607)` | 80 | 2 | 4 |
| Two equality scans, intersected | 40 + 40 | 1 + 1 | 2 + 2 |

Every variant returned the same ten content IDs, with matching columns/types and
complete ordered digests, before/after compaction and unrelated append. The
literal-ID control rules out vocabulary resolution as the primary explanation.
The join actually has a dynamic filter:

```text
optional: term_id IN (504,607)
AND optional: term_id>=504 AND optional: term_id<=607
```

The shared bench's compact `scans` summary currently retains `Filters` but not
`Dynamic Filters`. Its earlier `filters: null` was therefore NOT evidence of no
pushdown. The diagnostic report separately preserves both fields from full plans.

The original 74-file run used concurrent ID allocation plus replay, rather than
this fixed-order reference layout. Its disposable files were removed, so the
exact six-file difference cannot be audited retrospectively. Admission order
changes IDs/ranges and replay changes file membership. The new 80-file result
is exactly explained by two selected buckets times forty batches.

### Secondary cost: optional filters emit excess rows

Before compaction, the vocabulary join's postings scan emits 560,010 rows into
the join, although only 20 postings match. The literal IN scan emits 584,994 rows
before an exact filter above it. Two equality scans emit ten rows each. Thus
known literal IDs alone do not remove the excess scan output: predicate form
also matters. This is an engine optimization opportunity, not incorrect results
or a reason to duplicate the dictionary. See `UPSTREAM.md`.

Compaction merges 40 files per bucket into one, leaving eight files total. The
target still occupies two buckets. A fresh unrelated append creates eight new
files and makes each target bucket contribute one extra overlapping file again.

Compaction is NOT a demonstrated latency fix: in the first query order, the
join's warm profile was 8.27 ms before and 11.38 ms after compaction (12.38 ms
after append). Literal IN was 6.40/10.49/10.48 ms; equality intersection was
4.56/11.15/11.31 ms. Fewer, larger files can still involve substantial row-group
decoding/filtering and partial-snapshot metadata. Byte counters are warm/cache
observations; this experiment does not isolate those remaining costs. Do not
equate file-count improvement with less decoded data or faster execution.

### Consequence

Dictionary maintenance is separate from the demonstrated lookup bottleneck.
The next physical-layout experiment should target overlapping term ranges and
row-group selectivity under continued appends, with bounded compaction cost.
Simply increasing content buckets, sorting each incoming batch, or substituting
literal IDs will not solve the observed file fan-out. No production optimizer
rewrite, layout change or maintenance command was deployed.

Follow-up validation: the complete diagnostic passed its same-snapshot and
cross-stage result assertions; `make check` passed again (738 backend tests,
34 skips, five SDK tests, and package/frontend checks/tests/builds).

## Unpartitioned layout comparison (2026-09-11)

The proposed `material.term`/`material.postings` naming does not affect this
physical experiment: the disposable tables retain their original
`vocabulary`/`term_stat` names and identical column types to isolate layout.
Vocabulary remains unpartitioned and sorted by its unique term; postings sort
by `(term_id, content_id)` in every variant.

Tested three configurations on the same 20,000 synthetic contents, with identical
serial ID allocation, 500-content batches, ten fixed matching contents, and
eight subsequent unrelated batches (4,000 additional contents):

1. Eight hash buckets, default 122,880-row groups.
2. No partitioning, default 122,880-row groups.
3. No partitioning, 8,192-row groups.

The existing diagnostic accepts these options:

```sh
# Run from packages/periplus. Repeat with buckets=8/default groups, and
# buckets=0/8192 groups, using a different report path for each.
uv run python ../../benchmarks/query/experiments/vocabulary_lookup.py \
  --contents 20000 --postings-buckets 0 --row-group-size 122880 \
  --append-batches 8 \
  --report ../../.artifacts/query-benchmarks/layout-plain.json
```

All 72 shared-bench measurements (three layouts, four stages, three SQL forms,
two query orders) returned the same ten IDs, column types and ordered digest.
This is the same local synthetic corpus and memory/thread settings as the prior
diagnostic. Results are warm profiles, not cold object-store measurements.

| Layout | Total files before compaction | Join files before / compacted / +8 appends | Warm join ms before / compacted / +8 appends |
| --- | --- | --- | --- |
| Hash buckets, default groups | 320 | 80 / 2 / 18 | 9.31–10.06 / 11.80–12.12 / 11.90–12.19 |
| Unpartitioned, default groups | 40 | 40 / 1 / 9 | 9.90–10.18 / 5.68–6.05 / 6.99–7.08 |
| Unpartitioned, small groups | 40 | 40 / 1 / 9 | 8.51–9.13 / 5.95–6.11 / 6.22–6.36 |

Removing hash buckets halves lookup file fan-out for this two-term query and
reduces write file fan-out eightfold. It does NOT remove per-batch overlap:
unpartitioned lookup grows from one compacted file to nine after eight appends.
Without compaction, unpartitioned/default scanning is not faster than hashing.

### Row groups and storage

The new footer evidence counts row groups whose ranges can contain either exact
term ID, and sums their rows and compressed term-ID/content-ID column bytes.
These are metadata-derived candidate amounts, NOT observed I/O. In particular,
optional IN/dynamic filters may use broader intervals and scan more groups.

| Layout | Compacted groups potentially matching exact IDs | Rows in those groups | Compressed candidate column bytes | Entire postings files before → after compaction |
| --- | --- | --- | --- | --- |
| Hash/default | 2 | 219,653 | 7,428,136 | 11.57 → 53.05 MB |
| Unpartitioned/default | 2 | 236,810 | 3,017,462 | 1.77 → 26.64 MB |
| Unpartitioned/small | 2 | 16,384 | 1,034,967 | 10.15 → 129.78 MB |

At the scan operator, compacted vocabulary-join output was 441,977 rows for
hash/default, 196,320 for unpartitioned/default, and 4,788 for small groups.
Only twenty postings ultimately match. Smaller groups materially improve
filtering, but did not improve compacted latency and substantially increased
storage. Do not assume a single smaller-file-count generation is also smaller
in bytes. This corpus is unusually repetitive; the encoding/partial-snapshot
causes of the compaction storage expansion have not been isolated here.

Initial build times were 8.42 / 7.82 / 8.06 seconds respectively, with median
serialized commits 59.8 / 46.9 / 51.5 ms. The one-time compactions took
0.43 / 0.31 / 0.44 seconds. They are local observations, not sustained production
capacity or long-term write-amplification measurements.

### Decision

Keep unpartitioned postings sorted by `(term_id, content_id)` with the default
row-group size as the next experimental baseline. It outperformed hashing after
compaction, used fewer files, and used less postings storage in this fixture.
Do not select small row groups yet: their small post-append latency benefit does
not justify the observed storage growth.

This does not satisfy the bounded-growth acceptance criterion: every fresh batch
still adds a file that a rare lookup must consider. Coarse term ranges, larger
corpora with realistic term frequencies, and sustained maintenance cost remain
unmeasured. No production schema, layout or worker configuration was changed.

Validation: the three focused DuckLake tests passed, including identical logical
postings across layouts and actual sorted multi-row-group Parquet output.
`make check` passed (739 backend tests, 34 skips; five SDK tests; package and
frontend checks/tests/builds). No generated reports or lake files are committed.


## Lifecycle integration

The private registry now declares `vocabulary` at generation grain and `term_stat`
at content grain. Preparation reserves compact IDs using the existing generation
claim before writing sorted immutable postings files; normal atomic replacement and
Postgres receipts publish the content projections. See [LIFECYCLE.md](../../LIFECYCLE.md)
and [SCHEMA.md](../../SCHEMA.md) for ownership, replay and tokenizer contracts.

Real local DuckLake tests exercise interrupted preparation, dictionary rollback,
overlapping reservations, failed file registration, lost receipts, hidden-generation
ID independence and stale live-generation rejection. Retention removes content
frequencies but leaves shared dictionary entries. This does not establish sustained
distributed ingestion performance or bounded discovery cost at billion-capture scale.


Container verification was attempted locally but stopped at the initial Debian
package installation: the Docker VM filesystem had zero available space.
The ICU source archive was downloaded separately and its pinned SHA-512 verified.
The runtime Docker build still needs to pass on a host with available disk space;
this integration has not been deployed.

Validation: `make check` passed after integration (753 backend tests, 34 skipped;
SDK tests and package/public/admin checks and builds passed). The eight focused
term lifecycle tests also passed independently. Local check output:
`.artifacts/query-benchmarks/term-lifecycle-check.log`.


## Public catalogue exposure

The registered `public_v1.term(content_id, text, frequency)` view now joins
content frequencies to the vocabulary, without exposing dictionary IDs or unused
reservations. QueryService contract tests cover exact and ILIKE matches, term
co-occurrence, prose/capture/heading joins, column types, and both query modes.
The existing compiler rules keep their previous scope; this change adds no
optimizer rewrite or pruning guarantee. Deployment and a complete generation
rebuild are still required for an existing lake.
