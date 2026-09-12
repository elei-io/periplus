# Append-only text index layout comparison

Classification: schema/catalogue access path. Research only; no production changes.

Read the [results and recommendation](results.md) and [complete scorecard](scorecard.md).
The subsequent [mapping/deduplication follow-up](mapping.md) tests a cheaper
construction path specifically for option 2.

Compare term/batch nested postings, term/content node lists, and flat term/content/node
rows using identical matches derived from the retained 500-content canonical element
fixture. Final measurements use PyICU 2.16.2, ICU 77.1, Unicode 16.0, root-locale
word breaks on each original full-page text exactly once. Each emitted term key is
case folded and NFC-normalized, retaining the occurrence's original code-point
offsets. Map occurrences to every fully containing element and deduplicate
term/content/element matches. Preliminary regex and per-element ICU runs are
excluded. This is not a substring accelerator.

Hypothesis: packing reduces bytes and write cost, but predicate pushdown, nested-list
expansion, file overlap and native sorting determine selective query work. Native
compaction must not be confused with regrouping equal term rows.

Use the shared query bench deadline, bounded result collection, result digests and
profile inspection. Compare complete ordered answers, reverse measurement order,
record absent physical metrics as unknown, and distinguish fresh connections from
cold storage. Build only disposable local DuckLake tables; native INSERT costs exclude
production network, NATS, Postgres claims and receipts. Tokenization is measured once
separately, so each layout receives exactly the same input.

Matrix: unpartitioned and 8/32 term buckets, term-first sorting, all three shapes;
fresh appends and native sorted compaction. Follow with independent corpus-size and
batch-count growth. Hold rare-word matches fixed during unrelated corpus growth.
The main matrix uses the 122,880-row Parquet default; a 2,048-row-group comparison
tests whether coarse row groups obscure differences between the packing shapes.
Results and raw fixtures remain in ignored local artifacts.

## Tokenization boundary

The user selected full parsed page text as truth, including title text already
present in that stream, with no added metadata attributes. This deliberately does
not model independent per-element word boundaries, CSS visibility, main-content
extraction, or arbitrary substring matching. Script/style text remains included
because the current parser contract includes it.

The retained sample has 66,121,409 document-root text characters versus 335,372,213
characters summed over elements (5.07 times as much text). For
`<p>cat<span>fish</span></p>`, the page token `catfish` maps to `p` and its containing
ancestors; the span does not independently introduce `fish`. Tests cover original
offsets across astral characters, combining marks, and case-fold expansion.
The prototype performs a batch-local interval join and DISTINCT to map occurrences;
that construction cost is measured separately from index packing/publication.

## Reproduction

From `packages/periplus`, using a fresh ignored output directory:

```sh
uv run --with PyICU==2.16.2 python ../../benchmarks/query/experiments/index-layout/compare.py \
  prepare --fixture /path/to/canonical-fixture/metadata.duckdb \
  --output ../../.artifacts/index-layout/icu-source
uv run python ../../benchmarks/query/experiments/index-layout/campaign.py \
  --root ../../.artifacts/index-layout --phase matrix
uv run python ../../benchmarks/query/experiments/index-layout/campaign.py \
  --root ../../.artifacts/index-layout --phase rowgroups
uv run python ../../benchmarks/query/experiments/index-layout/campaign.py \
  --root ../../.artifacts/index-layout --phase growth
uv run python ../../benchmarks/query/experiments/index-layout/campaign.py \
  --root ../../.artifacts/index-layout --phase fragmentation
uv run python ../../benchmarks/query/experiments/index-layout/campaign.py \
  --root ../../.artifacts/index-layout --phase files
uv run python ../../benchmarks/query/experiments/index-layout/campaign.py \
  --root ../../.artifacts/index-layout --phase appendgrowth
uv run python ../../benchmarks/query/experiments/index-layout/http_reads.py \
  --root ../../.artifacts/index-layout
uv run python ../../benchmarks/query/experiments/index-layout/projection_probe.py \
  --root ../../.artifacts/index-layout
uv run --with PyICU==2.16.2 python ../../benchmarks/query/experiments/index-layout/test_semantics.py
uv run python ../../benchmarks/query/experiments/index-layout/summarize.py \
  --root ../../.artifacts/index-layout
```

The script rejects a different ICU/Unicode pin. Maintenance uses production's exact
image digest `sha256:4ed6020562d906d1fb679c2125b776c0bfa378fadd8890310b892e27110a0841`,
confirmed read-only against the homelab Deployment. Containers have no network, two
CPUs, a 4 GB DuckDB limit, and a 6 GB container limit. Only the new disposable lake
directory is mounted writable. Table settings and the native merge call do all
maintenance; no application-aware aggregation or list updates occur.

For additional native passes, run `settle.py /experiment` inside the same pinned
container, mounting one disposable variant directory at `/experiment` and the
script read-only. Match the limits and extension loading used by `compact.py`.
`followup.json` records each native response, active file counts and before/after
snapshots. Do not run this against the production lake.

The `prepare` fixture must have the current canonical `material.html_elements`
columns in DuckLake metadata schema `ducklake`. Its full parsed text and offsets
are the source contract. Builds use native local INSERT to isolate the three
physical shapes; they do not reproduce the complete production publication path.
HTTP probes count response bytes instead of interpreting profile footer counters
as total I/O. Fresh physical metadata copies are retained before native maintenance.
