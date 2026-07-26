# Atlas analytical ground truth

This directory is the complete, removable Atlas analytical ground-truth pack.
It generates deterministic web pages, loads them through Atlas's normal raw-HTML
and DOM-ingestion boundary, and judges authored SQL against truth that is never
written to the lake.

Implemented scenarios:

- `product_market` contains 24 products, 12 independent retailer domains, four
  offers per product, and 10 observation points over 14 days (960 crawls).
- `claim_lineage` contains three measurable claims on 30 publication domains
  across three observation points (90 crawls). Its controls distinguish eight
  independent origins from 12-domain and 10-domain repetition rooted in one
  source.

Page content changes less frequently than crawls, so ordinary content
addressing creates document reuse. Run the product-market scenario from the
repository root:

```sh
make analytical-ground-truth-test
make analytical-ground-truth-plan
make analytical-ground-truth-load
make analytical-ground-truth-verify
make analytical-ground-truth-run
```

Run claim lineage with:

```sh
make analytical-ground-truth-claim-plan
make analytical-ground-truth-claim-load
make analytical-ground-truth-claim-verify
make analytical-ground-truth-claim-run
```

The claim query derives a fact key from station, measurement, and date in
article prose, extracts normal `rel="cite"` links, and recursively walks their
ancestry. It proves the raw-lake lineage mechanism; it does not claim to solve
general natural-language claim equivalence or infer undeclared citations.

`load`, `verify`, and `run` refuse every lake except `atlas_test`. Passing
`--lake atlas_load --allow-load-lake` is the explicit exception for overlaying
the small semantic pack on the scale corpus. The production `atlas` lake is
always refused.

Generated HTML, DOM Parquet, and query results are temporary and are not
committed. Production code does not import this package.
