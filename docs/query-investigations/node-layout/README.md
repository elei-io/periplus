# Unified nodes and term provenance

Classification: schema/catalogue. No optimizer rewrite is introduced.

Hypothesis: one materialized node table removes duplicate element structure and one
file family per batch while preserving the public element contract. Content-clustered
node postings locate text matches before DOM extraction. Neither implies file pruning
under continued appends; measure that separately.

Layout: html_nodes retains attributes and direct text on element rows, null otherwise.
html_element filters those rows. material.term(text,term_id) replaces vocabulary;
content_posting replaces term_stat with unchanged document frequencies. node_posting
stores (term_id,content_sha256,node_index,frequency), sorted by content, term, node,
without partitions. The public term remains unchanged; term_node exposes text-node
matches and frequencies. Existing generation finalization removes obsolete tables.

Batch context computes prose and both frequency maps once per content. ICU case folding
and NFC use normalization-safe segments, mapping UTF-16 word spans to all contributing
text nodes. Block separators and excluded subtrees match existing prose semantics.
Node frequency counts intersecting occurrences, not independent node tokenization;
frequencies are not additive across nodes. No corpus-global provenance cache.

Tests cover inline splits, decomposed accents across nodes, Hangul composition,
case-fold expansion, astral Unicode, multilingual segmentation, direct-text/attribute
view equality, shared computation, structural matching, and existing rebuild/retry
coverage. Run the registered node-layout query via the shared bench after activation.

Remaining production gate: deploy with a complete generation rebuild, observe the
first 1,000-visit batches for RSS/retries, and verify the public SQL after activation.
The measurements below establish a bounded trial, not a worst-case memory guarantee.

## Measurements (2026-09-11)

A read-only, SHA-256-ordered sample of 1,000 distinct retained UTF-8 HTML objects
contained 237,022,305 source bytes. Source data and full profiles remain ignored local
artifacts; no production catalogue was changed. Existing document token counts matched
for every sampled document, including normalization and ICU segmentation.

The candidate prepared 2,211,350 nodes, 462,629 content postings and 1,255,848 node
postings in 70.35 seconds. Peak process RSS through preparation was 2.61 GiB on macOS;
this includes parsing and Arrow preparation of the measured projections, not a complete
production worker with every visit projection and remote writes. Whole comparison RSS
was 4.15 GiB while retaining both layouts and running the local DuckLake benchmark.
This supports trying 1,000 visits within the existing 8 GiB worker limit, with monitoring;
it does not prove safety for unusually large pages or every batch.

Before retaining the normalized element tag column, single-file Zstd Parquet DOM totals: separate nodes/elements 83,337,592 bytes;
unified nodes 77,176,792 bytes (7.4% less). New node postings added 3,809,695 bytes,
leaving DOM plus node postings 2,351,105 bytes smaller than the prior DOM alone.
These are controlled encoding sizes, not production S3 or metadata totals.

Both-order query results matched. The canonical run used two threads, 512 MB DuckDB
memory and the explicit `n.node_type='text'` restriction in the checked-in case.
Warm cached byte counters were zero and must not be interpreted as zero storage I/O
for uncached queries. This does not establish append pruning or corpus-scale latency.

An earlier synthetic probe without the text-node predicate regressed substantially
under unified-table self-joins. Explicit node-kind filtering reduced that gap; changing
to node-type partitioning did not help and was rejected. Keep the existing content
buckets and sort order. This is a storage/convenience tradeoff, not a claim that every
query becomes faster. No optimizer pass is disabled or upstream change required.

Validation: full `make check` passed (755 backend tests, 34 environment-dependent skips,
SDK checks and all frontend checks/builds); 27 focused tests also passed, including the
additional real-DuckLake node-posting commit, replay and replacement case.

Paired order 1: separate warm 52.81 ms; unified warm 62.54 ms, 41 complete result rows.

Paired order 2: separate warm 51.52 ms; unified warm 61.11 ms, 41 complete result rows.
