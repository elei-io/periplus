# Query optimization ledger

This ledger records decisions produced by the real-user scenarios in
[`benchmarks/query/`](../benchmarks/query/). Raw profiles remain local or CI artifacts; accepted
reports record their DuckLake snapshot and exact result digest.

## Initial unified-suite smoke run

The first run used DuckDB 1.5.5, Atlas catalogue 5.0.1, extension build `286e2dc`, and DuckLake
snapshot 24855. Each query ran normally once and then once warm through JSON `EXPLAIN ANALYZE`.

| User scenario | Scale | Normal | Warm | Reported scan work | Result rows |
| --- | ---: | ---: | ---: | ---: | ---: |
| Recent ingestion activity | — | 118 ms | 9 ms | 47,384 | 50 |
| Exact page history | — | 167 ms | 31 ms | 94,768 | 1 |
| Current domain inventory | — | 72 ms | 18 ms | 47,384 | 3 |
| Python external destinations | — | 150 ms | 13 ms | 7,659,484 | 30 |
| MDN main-link extraction | 100 contents | 6.05 s | 2.46 s | 142,152 | 3,260 |
| Current-page image accessibility | 100 per domain | 2.47 s | 375 ms | 43,324,112 | 3 |

DuckDB's cumulative scan metric can count work across projected columns and should not be treated
as a physical table row count. Per-operator cardinalities and file counts remain in the JSON report.

## Next candidate: direct element joins over a bounded content scope

The new `current-page-image-accessibility` scenario asks a user-relevant question: how many current
documentation images have missing or empty alternative text? A scope of 295 distinct content
identities joined to `dom.element` and produced 1,095 selected image rows before the final
three-domain aggregation.

The `html_elements` scan nevertheless:

- opened all 16 files;
- reported 43,181,960 scanned column-rows;
- emitted 136,755 globally matching `img` rows before the content join; and
- was reduced to 1,095 rows only by the later hash join.

This is classified as an optimizer gap. The immutable content scope is already bounded and the
existing physical layout supports exact content probes, as demonstrated by the native selector
operator. The next experiment should determine whether the same native runtime-key scan mechanism
can serve direct `dom.element` joins while preserving arbitrary projections, filters, duplicate-key
bag semantics, and global SQL operations. No new material relation is justified by this evidence.

A same-snapshot repeat produced the identical ordered result digest. The comparison runner reported
an exact result match, a scan-work ratio of 1.0, and a warm-time ratio of 1.17, validating the
baseline/regression path before optimizer work begins.

## Direct element join experiment: bounded runtime-key batches

The native experiment consumed runtime `content_id` keys in bounded batches, projected only the
needed element columns, pushed simple element predicates into the physical scan, and restored
duplicate-key bag semantics after scanning. Deterministic tests compare every element column with
the ordinary join using `EXCEPT ALL`; duplicate, NULL, and missing keys are covered separately.

At DuckLake snapshot 24856, the unchanged public query and the experimental execution produced the
same ordered digest at every scale:

| Pages per domain | Ordinary warm | Keyed-batch warm | Ordinary peak buffer | Keyed-batch peak buffer |
| ---: | ---: | ---: | ---: | ---: |
| 50 | 328 ms | 653 ms | 559 MiB | 416 MiB |
| 100 | 409 ms | 1.19 s | 559 MiB | 509 MiB |
| 250 | 322 ms | 2.31 s | 559 MiB | 553 MiB |

The per-document version took 4.31 seconds warm at scale 100; pushing `tag = 'img'` into each
individual probe made it worse at 7.24 seconds because dataset-open overhead dominated. Batching
removed those repeated opens and reduced that result to 1.19 seconds.

This is useful execution evidence, but not sufficient evidence for a transparent optimizer rewrite.
On the present corpus the ordinary global scan is 2–7 times faster, and the batch path's memory
advantage disappears as the selected scope grows. DuckDB's outer profile does not include the
nested physical scans, so its reported 142,152 rows cannot be compared with the ordinary plan's
43,324,112 cumulative column-row count. Keep the public query unchanged and do not activate this
rewrite until a larger-corpus fixture demonstrates a clear capacity crossover and supplies an
auditable nested-scan metric.

## Selector experiment: relational document stream

The generic path already exists for ordinary SQL. After `dom.element` views and user SQL macros
expand, DuckDB creates an exact runtime `IN` filter from a bounded `content_id` join and DuckLake
uses it for bucket pruning. A four-key runtime join and the equivalent literal filter both read the
same four files from two selected buckets, rather than all sixteen files. Atlas should not add
per-macro rewrites for this case.

CSS selection is an opaque native boundary. A research plan replaced its private per-document
physical probes with a generic `scope JOIN dom.element`, added explicit document-end sentinel rows,
ordered the resulting stream by content and element index, and passed that stream to the existing
storage-independent `atlas_dom_select_all` operator. At snapshot 24856 it produced the exact same
ordered result digests as the public selector query:

| Documents | Keyed selector warm | Relational stream warm | Keyed peak buffer | Relational peak buffer |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 2.27 s | 3.21 s | 751 MiB | 844 MiB |
| 500 | 12.73 s | 5.62 s | 976 MiB | 965 MiB |
| 1,000 | 22.17 s | 8.39 s | 973 MiB | 974 MiB |

The shared relational path has a much better latency slope but regresses the small interactive
case and requires a global ordering boundary. Atlas explicitly accepts the roughly three-second
100-document latency in exchange for making larger selector jobs practical, so runtime thresholding
is not required. The implementation must nevertheless be a compiler plan rewrite: directly
expressing the relational stream inside the correlated public macro caused per-invocation ordering
and exceeded one minute at scale 100. The compiler should capture the lateral scope once, construct
one dynamically filtered and spillable document stream, and feed the existing storage-independent
selector operator while preserving the caller's global SQL operations.

### Accepted: compiler-lifted relational selector stream

The extension now recognizes a safe correlated `atlas_dom_select_first_keyed` or
`atlas_dom_select_all_keyed` plan after public views and SQL macros have expanded. When it can prove
that every invocation is correlated only by the same content identity, it lifts the physical DOM
read out of the per-document boundary, dynamically joins the runtime scope to the element scan,
orders the combined stream by content and element index, and feeds the existing selector operator.
Unsupported or ambiguous plan shapes remain on the original exact implementation.

The unchanged public SQL was measured again in the release build against snapshot 24856. Every
scale produced the same ordered result digest as the pre-rewrite query:

| Documents | Before warm | After warm | Change | Before peak buffer | After peak buffer |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 2.27 s | 2.98 s | 1.32x slower | 751 MiB | 896 MiB |
| 500 | 12.73 s | 5.37 s | 2.37x faster | 976 MiB | 971 MiB |
| 1,000 | 22.17 s | 8.14 s | 2.72x faster | 973 MiB | 885 MiB |

The 100-document regression is the accepted capacity tradeoff. The ordinary relational scan is
visible to DuckDB's profiler while the old nested keyed scans were not, so the reported scanned-row
counts are not comparable across the two implementations and must not be interpreted as a read
amplification regression.
