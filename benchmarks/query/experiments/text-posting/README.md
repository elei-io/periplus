# Text-key positional postings

Classification: internal schema/layout and publication lifecycle. The public search
contract is unchanged. Numeric allocation serializes preparation and repeatedly
checks corpus membership; replacement deletes also touch existing files even when
no rows match. This experiment isolates key representation before removing allocation.

`compare.py` uses the shared benchmark's deadline, bounded collection, canonical
result digest and profile inspection. Input is the retained local 500-document
Lexbor `posting.arrow` and `term.arrow` fixture. No production reads or writes.
Ten appends reuse the same payload with disjoint synthetic content identities;
this tests overlapping files, not increasing language diversity. Fixtures and raw
results stay outside Git. Two CPU threads, 512 MB DuckDB memory; local Parquet/ZSTD.

The literal-key run produced these total sizes across ten files:

| Key | Compressed bytes | Encoding + local write |
| --- | ---: | ---: |
| Numeric | 192,224,718 | 6.28 s |
| MD5 UUID (comparison only) | 221,228,238 | 8.38 s |
| Normalized text | 208,917,818 | 7.01 s |

Text was 8.7% larger than numeric; hash was 15.1% larger. All equality and two-term
queries produced identical complete results. Single-term first executions across
1/5/10 appends took 2.1/4.2/6.1 ms numeric and 4.2/10.5/17.0 ms text. These are
single local samples, not cold-storage measurements or production predictions.
Numeric IDs in this fixture follow lexical order, favoring adjacent-key ranges.

Profiles showed literal scan filters, but all 1/5/10 overlapping files were read.
Warm profile bytes were zero due caching and do not measure physical object reads.
The row-scan counter does not establish decoded row-group counts. Thus key choice
alone does not solve append file fan-out or prove billion-capture scalability.
Compaction and clustering remain separate required performance work.

Decision: normalized text directly in postings, sorted by `(text, content_sha256)`.
Keep the existing single-file-per-batch layout in this release rather than introduce
unmeasured partition fan-out. Search issues normalized literal posting predicates
and no longer scans/translates vocabulary. Internal `material.term(text)` is a
lazy distinct view: coherent immediately with posting publication/retirement and
zero additional ingestion writes. Full vocabulary enumeration remains a projected
scan/external aggregation; it is deliberately outside search. A physically compact
vocabulary would require a measured incremental consolidation lifecycle.

Run from `packages/periplus`:

```sh
uv run python ../../benchmarks/query/experiments/text-posting/compare.py \
  --fixture /path/to/retained-lexbor-output --output /tmp/fresh-text-comparison
```

The output directory must be fresh for an append comparison; reusing it includes
files from earlier runs and invalidates intermediate growth measurements.
