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
