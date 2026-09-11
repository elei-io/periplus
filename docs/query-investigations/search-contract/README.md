# Page discovery and deterministic HTML text

Classification: schema/catalogue design, plus a parser correction for the public
one-argument SEARCH table macro. No term-DISTINCT optimization or element-predicate
postings acceleration is included. The side branch `codex/public-term-surface`
is superseded and was not merged.

The active generation is `840ca113-8141-4766-b920-82185a2500bc`, completed with
200 batches and 193,990 visits. The physical registry remains
`a1dc72023b788bdba14fa07073efb79f56f76a7b38007e6786f84732d6c03a63`.
No materialization declarations, row builders, ownership keys or layouts change.
The public install drops removed objects and publishes views/macros atomically.
Deploy with the coordinated setup sequence; no rebuild or data migration is needed.

## Contract and hypothesis

See [SCHEMA.md](../../SCHEMA.md#public_v1searchquery) and the registered helper
metadata for exact semantics. Search is initially a literal normalized substring
scan, not an inverted-index or semantic-search promise. Element text includes every
parsed descendant text node and cannot use body-only postings as a complete filter.

Correlated element-text aggregation should let a content/tag restriction reach the
outer node scan before descendant reconstruction. Unused text should be eliminated
from structural queries. Fixed-content access is compared across retained-source
samples of 100 and 1,000 documents, using the shared benchmark and 512 MB/two threads.

## Measurements

`benchmarks/query/experiments/public_search.py` creates a disposable local DuckLake
from retained UTF-8 HTML. Source objects and raw reports remain ignored artifacts.
On 1,000 retained contents, the combined-scan candidate returned 100 results in
399 ms on the first measured execution and 547 ms warm. A nonempty selected-content
h1 predicate returned one independently verified row in 11.3 ms / 11.4 ms warm,
reading one nodes file on each side. Warm byte counters were zero because of caching;
they do not prove zero physical I/O. The earlier 100-content probe took 84 ms warm.
These local files are not a production sizing model.

Read-only production probes used temporary in-memory catalogue definitions against
active snapshot 346888, DuckDB 1.5.5 / DuckLake d8a1881e. No persistent catalogue
objects were changed. Prose-only literal counting took 5.9 seconds (one thread,
1.8 GiB effective memory). With two threads and 512 MB configured memory, counting
title/description declarations took 86.1 seconds, distinct capture content lookup
0.68 seconds, and a nonempty fixed-content h1 text query 0.48 seconds.

The combined title/description condition leaves tag filtering above the scan. On the
local 1,000-content profile, the scan emitted 1,045,165 element rows. Separate exact-tag
branches reduced that to 1,020 titles and 713 descriptions, with a 364 ms warm local
search, but exceeded 180 seconds in production. They read two sets of node files and
are not selected. The combined-condition full search took 146.8 seconds with two
threads / 4 GiB and returned 100 results, exceeding the live 120-second query policy.
It collects the display title during field aggregation, avoiding another node scan.
The final candidate also reuses prose's existing whitespace normalization rather than
normalizing it again; title and description whitespace still require normalization.
The final candidate also exceeded the 120-second production deadline. Deployment
is blocked; correctness and small local timings do not establish production readiness.
A compact content-owned title/description projection is the next layout candidate;
that changes the physical registry and requires a rebuild, outside the current
unchanged-registry constraint. Corpus discovery scans fields and
grows with corpus size; the 100-result cap does not bound input I/O.

Correctness fixtures cover split words, preserved whitespace, Unicode NFC and
case behavior, literal wildcard characters, empty/missing values, script/style/title
coverage, capture deduplication, ranking ties and output bounds. Existing compiler
families are retained only when their public inputs still exist. Retired prose
compiler experiments are removed from normal execution and case discovery.
