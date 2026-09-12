# Experimental query API input barrier

Historical investigation: the public prose/term surfaces and associated compiler paths
were removed by the search contract release. Commands below describe the earlier revision;
the old query cases now live under `benchmarks/query/retired/`.

Status: local implementation and tests, not deployed or promoted (2026-09-11).
Classification: optimizer, following [extraction-pruning.md](extraction-pruning.md).
The public relation grain and physical layout are unchanged.

## Intervention

The existing query API content-scope transformation now has an optional OFFSET 0
boundary on each restricted primitive input. This preserves all selected rows while
preventing downstream view predicates from being pushed below the content semijoin.
Both inputs are materialized before the actual reviewed heading SQL reconstructs text.
All native optimizer passes remain enabled. The existing stable rewrites are unchanged.

Compiler `public-query-v8` gates `prose_heading_input_barrier_v1` on experimental
mode only. Eligibility is a two-table prose/heading inner join on content identity,
one qualified prose-text equality/LIKE/ILIKE predicate, string bindings, and the
shared conservative grammar. No outer joins, CTEs, LIMIT/OFFSET or other target
views. Original SQL binds first; installed definitions must match the reviewed
catalogue. Unsupported statements use normal execution. Both prepare and execute
select the candidate; responses retain original SQL and parameters.

```sql
SELECT h.content_id, h.node_index, h.level, h.text
FROM prose p JOIN html_heading h USING (content_id)
WHERE p.text ILIKE '%monkeys%'
ORDER BY h.content_id, h.node_index;
```

`term` remains an experimental materialization, outside the production registry.
The term branch of the disposable benchmark applies the same input barrier to its
research SQL; it is not a claim that QueryService accepts `term`. The prose branch
uses the actual API compiler function on unchanged valid public SQL. API integration
tests separately execute unchanged SQL through read-only stable and experimental
QueryService connections. No public-schema bypass was introduced.

## Controlled results

Reproduce with the ICU prerequisites in [term-surface.md](term-surface.md):

```sh
cd packages/periplus
uv run python ../../benchmarks/query/experiments/term_extraction.py \
  --scales 100 1000 5000 \
  --report ../../.artifacts/query-benchmarks/term-extraction-barrier.json
uv run python ../../benchmarks/query/experiments/term_extraction.py \
  --scales 1000 --check-broad \
  --report ../../.artifacts/query-benchmarks/term-barrier-broad.json
```

Same synthetic corpus, engine, memory/thread settings and append/compaction protocol
as the preceding investigation. Comparisons run at one snapshot in both orders;
all 336 measured variant results agree within their family, and selected keys and
results stay fixed across growth. Timings are local warm profiles, not cold storage
or production-service latency. Physical output counts are not bytes decoded.

For one fixed content with 14 headings:

| 5,000-content state | Prose native ms | API candidate ms | Term native ms | Term barrier ms | DOM files per table: native → candidate |
| --- | --- | --- | --- | --- | --- |
| After 40 unrelated append batches | 178–185 | 8.9–9.0 | 177–183 | 9.8–10.9 | 328 → 27 |
| Compacted | 137.6–137.9 | 7.6–8.8 | 133–145 | 8.9–9.3 | 8 → 1 |

On the compacted corpus, candidate scans emit 55 element and 107 node rows—the
complete selected content—versus 275,000 and 241,224 for native heading extraction.
The boundary intentionally leaves node-type filtering above content selection;
it reads a few more selected nodes than literal heading lookup, but preserves
whole-document semantics and prevents corpus-wide reconstruction.

For two fixed contents, the API candidate takes about 10.2 ms after compaction
versus 134–145 ms native. However, optional set filters still emit 64,075 element
and 126,902 node rows from two files. The rewrite helps reconstruction but does not
solve multi-key scan filtering. After appends, single-key lookup still opens 27
files although only one contains the selected content. Compaction reduces this
fan-out; neither case proves stable row-group bytes under continued growth.

The 1,000-content broad/empty check also preserves complete results in both orders.
All-content matching returns 14,000 headings, with both variants around 35–36 ms;
empty matching returns zero, around 18 ms native versus 4.3–4.4 ms candidate.
This smaller follow-up overlapped project checks, so these timings are exploratory,
not an isolated regression budget. Connection-level buffer high-water marks do not
establish per-query memory deltas. A representative broad workload remains required.

## Acceptance and remaining work

The subsequent [multi-key investigation](multikey-extraction.md) confirms bounded
heading reconstruction for 10/100/1,000 fixed matches, but not bounded scan work.
Smaller row groups and a higher native set-filter cutoff do not resolve that gap;
neither setting is promoted.

Differential tests cover duplicates, all/empty/selective domains, parameters,
columns/types/order, zero matches, empty headings and headings without the search
term. A physical regression requires both DOM scans to emit only the selected
content in the single-key fixture. QueryService tests cover stable exclusion,
experimental prepare/execute activation and catalogue mismatch rejection.

This is a useful experimental optimization, not the production search acceptance
gate. Broad discovery still scans prose; fixed-key append overlap and multi-key
optional filters remain. Before promotion, validate representative selectivities
and unchanged public SQL through the deployed query service. Before publishing
`term`, integrate its lifecycle and then evaluate extending driver eligibility;
do not expose its private dictionary/postings or disable optimizer passes globally.

Validation: `make check` passed (744 backend tests, 34 environment-dependent skips;
five SDK tests; package/frontend checks and builds). Focused content-scope,
QueryService and extraction tests also passed. No deployment was performed.
