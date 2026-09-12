# Bounded parallel preparation

Run `parallel_preparation.py` with `uv` from `packages/periplus`. Supply a local
fixture directory containing `manifest.json` (a list of `id`, `file`, `bytes`,
`compressed_bytes`) and `objects/<file>` containing canonical zstd HTML.
Fixtures are private local evidence, never committed.

The benchmark creates a fresh local DuckLake, seeds actual visit/attempt/document
evidence, and calls production `prepare_batch`, `commit_prepared_batch`, and
active-generation validation. Control claims, dictionary coordination, and
receipts use a disposable schema in **local Postgres**; its URL must use localhost.
No production connection is used. Only the preparation context is replaced in
`--mode sequential` to reproduce the prior batch-wide parser implementation.

```sh
PERIPLUS_CONTROL_DATABASE_URL=postgresql://periplus:periplus@127.0.0.1:55432/periplus_test \
PERIPLUS_DUCKDB_THREADS=1 PERIPLUS_DUCKDB_MEMORY_LIMIT=1GB \
PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE=4GB \
PERIPLUS_MATERIALIZER_PARSER_PROCESSES=2 \
uv run --with psutil python ../../benchmarks/materialization/parallel_preparation.py \
  --fixture /tmp/periplus-prefetch-experiment \
  --output /tmp/periplus-pipeline-parallel-500 --mode parallel
```

Use a new output directory and `--mode sequential` for the baseline. Compare all
six canonical Arrow fingerprints in `result.json`; these include every value,
type and null, with deterministic identity ordering and record-batch sizes.
Source seeding and fingerprint generation are outside the timed/RSS interval.
RSS samples sum the parent and parser children every 50 ms. This can count shared
pages more than once; it excludes the separate Postgres process and filesystem cache.

## Measured 2026-09-12

MacBookPro18,3, 8 logical CPUs, 16 GiB, Python 3.14.7, ICU 77.1.
500 retained documents, 124,845,164 source bytes. DuckDB: one thread, 1 GB memory,
4 GB spill. Local files and disposable Postgres 17.

| Phase | Previous sequential | Two parser processes |
| --- | ---: | ---: |
| Complete batch, including validation | 70.14 s | 34.79 s |
| Projection/source/dictionary preparation | 61.33 s | 25.86 s |
| Parquet write | 5.46 s | 5.42 s |
| Commit | 0.47 s | 0.45 s |
| Activation/public/registry validation | 2.85 s | 2.81 s |
| Peak combined process RSS | 2.19 GiB | 1.15 GiB |

All six fingerprints match exactly; both write 55,204,578 Parquet bytes:
149,907 terms, 1,150,660 nodes, 840,034 postings, 58,351 links, 460 JSON-LD records,
and 500 visit proofs. Registry digest remains
`bce22251a34629c976166429e6a6141d54ca65bf4b7bb567102a8554c385a68a`.

The result supports starting production with two parsers in its existing two-CPU,
one-lane materializer pods. It does not predict whole-rebuild duration: network
storage, writer contention, compaction and full-corpus validation still apply.

The three largest retained HTML objects (83,815,376, 83,809,301 and 47,103,160
bytes) also passed the complete parallel pipeline and all registry/public
validation. They ran alone under the source-byte reservation rule: 19.09 s total,
2.58 GiB peak combined RSS, 137,289,356 final Parquet bytes. This confirms the
oversized path on current corpus extremes; it is not a comparison against the
sequential baseline.

## Lexbor and retained preparation, 2026-09-12

The complete Lexbor adapter (including template fragments, namespace/attribute
extraction and processing instructions) was measured on the same fixtures and
budgets, with two parser processes. Compared with the preceding HTML5lib parallel
results:

| Workload | HTML5lib | Lexbor | Peak combined RSS, old → new |
| --- | ---: | ---: | ---: |
| 500 documents, complete pipeline | 34.79 s | 26.46 s | 1.15 → 1.01 GiB |
| Three largest documents, complete pipeline | 19.09 s | 5.75 s | 2.58 → 1.94 GiB |

The 500-document Lexbor run spent 17.73 s in projection/source/dictionary
preparation, 5.30 s in Parquet writes, 0.45 s committing and 2.71 s validating.
Two Lexbor runs produced identical fingerprints for all six projections. The new
tree contains 1,151,337 nodes, including explicit template fragments; old parser
identities are intentionally not an equality target. Other relation counts remain
149,907 terms, 840,034 postings, 58,351 links, 460 JSON-LD rows and 500 visit proofs.
The full native adapter's measured pipeline gain is 24%, not the prototype's
parser-only 7× gain. These are local results, not a whole-rebuild forecast.

Add `--dictionary-contention` to hold a real control-Postgres generation claim for
two seconds at the first dictionary reservation. A 500-document run passed with
six rejected acquisitions, **one preparation**, zero retained bytes after return,
and all six fingerprints identical to the uncontended run. Its 30.18 s total
includes the deliberately induced wait and is not a throughput comparison.
