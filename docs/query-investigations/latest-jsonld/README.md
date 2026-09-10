# Latest observations before JSON-LD extraction

Classification: compiler/optimizer; physical access may be a contributing issue.
Incidents: stable 523a3b15-b00a-4926-ac72-240ec323eb80 (33.90 s, snapshot 97950),
experimental 60602dcb-0e9e-4c64-8a31-3814c61ff31d (52.26 s, snapshot 97972).
Both ran the same SQL on 06e406c, with identical plans apart from estimates;
no custom rewrite applied. These different-snapshot timings are not a paired comparison.

Hypothesis: finish latest-successful capture selection once, derive distinct content
IDs and restrict HTML-element input before JSON-LD parsing. Compare an inline
restricted input with the same input materialized. Keep native settings unchanged.

Protocol: shared read-only production paired bench, one snapshot per pair, 2 threads,
512 MB DuckDB memory, 256 MB spill, 120 s total per variant. Repeat both orders.
Compare complete typed result bags: timestamp-only ordering permits ties. If LIMIT
boundary ties cause differences, verify all results without the final limit and
label that separate case explicitly. Preserve ranking filter placement, duplicate
scripts/content reuse, invalid JSON and the final post-filter limit.

No runtime activation or physical layout change until evidence justifies it.

## Results: 2026-09-10

Production reader pod used core 06e406c with the shared bench copied from the same
revision. Both sides in each pair shared one read transaction, two threads,
488.2 MiB effective DuckDB memory and 244.1 MiB spill; no native optimizer was disabled.
Every pair completed and returned the same 500-row typed result bag. Reverse-order
pairs also verified installed `html_jsonld` against the reviewed source in the
same transaction before execution. Ordinary runs are not claimed to be cold.

| Variant / order | Snapshot | Baseline ordinary | Candidate ordinary | Baseline profile | Candidate profile | HTML files, both sides |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Inline, baseline first | 98664 | 7.65 s | 29.50 s | 12.61 s | 29.44 s | 432 |
| Inline, candidate first | 99000 | 32.57 s | 14.49 s | 22.42 s | 32.39 s | 432 |
| Materialized, baseline first | 98787 | 15.08 s | 33.28 s | 26.22 s | 32.26 s | 439 |
| Materialized, candidate first | 99142 | 35.39 s | 18.46 s | 27.44 s | 28.08 s | 439 |

First ordinary executions were faster whichever variant ran first. Therefore
ordinary timings alone do not support a causal speedup. The candidate's profiled
execution was slower in both orders for each variant. Physical work did not improve:

- Inline: roughly 1.33–1.34 GB reported bytes read on both sides.
- Materialized: roughly 1.64–1.65 GB versus 1.33–1.34 GB for baseline.
- Neither introduced a content-ID predicate into the HTML-element storage scan;
  it remained a join above that scan. Script/media-type filters already applied.
- The materialized scan additionally projects tag, namespace and attributes into
  its intermediate, whereas the inline scan projects only content identity and
  direct text, using the other columns only for filtering. This is additional
  materialization work, not a smaller physical access path.

A follow-up inventory found 6,738 selected captures/content IDs out of 32,547
capture content IDs. This is a substantial domain, not a one-page lookup.
A diagnostic lookup of one known valid JSON-LD content, with a literal predicate,
returned three script rows in a 91 ms warm profile: 31 HTML-element files and
866,602 reported bytes read. The selected key was found immediately before that
profile, so it is not a cold measurement or a controlled comparison to the Tori
workload. It demonstrates that literal content filtering can prune reads here.
The profiler's rows-scanned counter still reported the large table-scale number
for that lookup; it must not be interpreted as actual decoded/processed rows.

## Correctness and decision

Adversarial local fixtures passed for both candidates: duplicate scripts, shared
content across listings, latest invalid price, latest non-Product, a newer HTTP
500 capture, invalid JSON/JSON null, and equal capture timestamps resolved by
capture ID. Five expected output rows were preserved, including multiplicities.
The shared benchmark's 16 tests passed, including binding the new public SQL case.

Do not activate either candidate. No API code, public catalogue, schema or storage
settings changed, and no performance PR is warranted. Classification now includes
an unresolved physical-access/filter-propagation question: a join-selected domain
is not producing the same scan predicate as a literal lookup, and a large scattered
selection may still need most files even with better filtering.

The next discriminating experiment is a same-snapshot comparison of the same
selected ID set delivered as a join versus an explicit scan predicate, including
key collection and compilation costs. Test small and large sets separately. Do
not infer from the one-ID result that a 6,738-ID set can skip the same proportion
of files. This evidence does not yet justify a layout change or persistent JSON-LD
materialization.

## Reproduction

Use the shared production-reader bench with `--case tori-latest-products`,
`--sql-override` pointing to this directory's `inline.sql` or `materialized.sql`,
`--warm-runs 1 --seconds 120`; repeat with `--candidate-first`. For a temporary
in-cluster reader label `--access-path cluster_direct_reader`. Both SQL files are
research inputs, not runtime compiler activation. Native profiles remain in ignored
local artifacts; only reviewed summaries belong in Git.
