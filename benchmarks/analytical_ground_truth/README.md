# Atlas analytical ground truth

This directory is the complete, removable `atlas-analytical-ground-truth-v1`
pack. It generates deterministic web pages, loads them through Atlas's normal
raw-HTML and DOM-ingestion boundary, and judges authored SQL against truth that
is never written to the lake.

The first scenario contains 24 products, 12 independent retailer domains, four
offers per product, and 10 observation points over 14 days (960 crawls). Page
content changes less frequently than crawls, so ordinary content addressing
creates document reuse.

Run from the repository root:

```sh
make analytical-ground-truth-test
make analytical-ground-truth-plan
make analytical-ground-truth-load
make analytical-ground-truth-verify
make analytical-ground-truth-run
```

`load`, `verify`, and `run` refuse every lake except `atlas_test`. Passing
`--lake atlas_load --allow-load-lake` is the explicit exception for overlaying
the small semantic pack on the scale corpus. The production `atlas` lake is
always refused.

Generated HTML, DOM Parquet, and query results are temporary and are not
committed. Production code does not import this package.
