# Append-only index comparison results

Measured 2026-09-13. Recommendation: continue with **option 2, one term/content
row containing distinct node indexes**, but do not yet call the billion-capture
access path solved. The tested native merge policy leaves overlapping term ranges
once a partition needs several files. No production code, schema, dependencies,
or deployment changed.

## What was compared

Thirty disposable DuckLake variants used exactly the same matches. The base was
500 retained real HTML documents, 594,873 elements, 158,021 terms, 849,151
term/content pairs, and 7,317,496 distinct term/content/node pairs.

| Option | Stored row | Sort order |
|---|---|---|
| 1 | `term, postings STRUCT(content_id, node_indexes[])[]` per batch | `term` |
| 2 | `term, content_id, node_indexes[]` | `term, content_id` |
| 3 | `term, content_id, node_index` | `term, content_id, node_index` |

Option 1 nests each content with its own nodes, avoiding ambiguous parallel arrays.
Option 3 is a distinct element match, **not every repeated word position**. All
three answer the same content/element identity queries; none stores phrase positions.

The matrix tested no partitioning, `bucket(8, term)` and `bucket(32, term)`;
122,880 versus 2,048 rows per row group; 10/100/400 appends; and controlled growth
to 1,000/2,000 content identities. Growth copies preserve the original distribution
with new content hashes, except the rare term is removed from copies to keep its
answer fixed at four contents and 27 nodes. This is synthetic growth, not 2,000
independently crawled pages or a billion-page extrapolation.

## Equal settings: writes and storage

500 contents, ten batches, eight term buckets, 122,880-row groups, 64 MB target
files, ZSTD. MB are decimal active Parquet bytes, excluding metadata, staging,
obsolete files and source HTML. Write time sums batch packing and native INSERT
publication, including sort/encoding; it excludes shared token preparation.

| Option | Index write seconds | Fresh MB | After native merge MB | Rare search after merge, ms | Actual HTTP bytes for rare search |
|---|---:|---:|---:|---:|---:|
| 1: term/batch | 3.02 | 11.35 | 10.78 | 10.37 | 1,358 KB |
| 2: term/content | **0.95** | 11.37 | 13.75 | 4.25 | 551 KB |
| 3: term/content/node | 1.82 | 16.53 | 28.31 | **2.49** | **92 KB** |

Option 2 was the cheapest to write. Option 1 saved maintained storage but spent
more time packing nested lists. Option 3 had more rows and bytes on disk, yet won
this selective query: its flat columns allowed cheaper reads. Row count alone is
not a measure of search work. Native rewriting can increase storage as grouping,
encoding and compression opportunities change; compaction did not always shrink it.

The nine query cases covered absent/rare/medium/common words, two-word content
intersection, rare/common node identities, a known-content restriction and LIMIT.
Local warm latency is not a production S3 latency estimate. Timings include complete
bounded result collection; common-node results can be dominated by output handling.

## Shared preparation is the bigger write cost

Update: the subsequent [mapping experiment](mapping.md) found a direct option-2
construction path taking about 2.63 seconds for mapping/deduplication/packing.
The measurements below retain the original layout comparison's preparation method.

ICU segmented each original page once: 66.1 million characters, rather than the
335.4 million characters obtained by independently reading every element's text.
PyICU 2.16.2 / ICU 77.1 / Unicode 16.0, root word boundaries, case folding then
NFC term keys, original code-point offsets. The experiment emitted 6,668,273
occurrences before mapping and deduplication.

| Shared operation | Seconds for 500 contents |
|---|---:|
| Read page text and tokenize | 6.25 |
| Build Arrow input, map occurrences to elements, deduplicate/store reference matches | 22.23 |
| Total | 28.49 |

This prototype's interval mapping is the larger cost to improve next. These figures
are not full materialization throughput: HTML was already parsed; production final
Parquet registration, claims, receipts, NATS, object storage and writer concurrency
were not exercised. Adding 0.95 versus 3.02 seconds after shared preparation does
not mean option 2 makes the whole materializer three times faster.

Title text is included because it is already in the parser's stream. Metadata
attributes are excluded. A word maps to every element fully containing its original
span. Thus `<p>cat<span>fish</span></p>` produces `catfish` on `p` and ancestors,
without inventing a separate `fish` match on the span. Script/style text follows the
current parser contract; this is not a CSS visibility calculation. Five semantic
tests cover this rule, title/metadata and Unicode offsets.

## Row groups and partitioning matter

With option 2 and eight buckets, reducing row groups from 122,880 to 2,048 rows
reduced rare-query HTTP bodies from 551 KB to 64 KB and warm time from 4.25 to
2.49 ms, while maintained storage rose from 13.75 to 22.69 MB. Smaller groups
were not universally better: option 3's many small groups increased footer cost
and made its rare search slower.

At the default row-group size, option 2's unpartitioned/8/32-bucket variants read
388/551/205 KB respectively for the maintained rare query. There is no monotonic
"more buckets is better" result here. Term-first sorting and term bucketing are
the appropriate candidate access path for term lookup; **eight is an experimental
setting, not a recommended lifetime bucket count for one billion captures**.

## Four hundred batches really can mean four hundred files

Option 2, same 500 contents, eight buckets and 2,048-row groups:

| Appends | Write seconds | Rare files before → after merge | Rare warm ms before → after |
|---|---:|---:|---:|
| 10 | 0.95 | 10 → 1 | 3.04 → 2.49 |
| 100 | 1.72 | 100 → 1 | 7.49 → 2.61 |
| 400 | 3.68 | 400 → 1 | 20.59 → 3.19 |

A search did not scan every row 400 times; it opened 400 relevant files and used
their filters. Native merging reduced 3,200 total files to eight. It preserved
all posting arrays and answers. No application-aware array update was needed.
Option 1's equal-term rows also remained separate rows after merging; native
maintenance does not combine their nested lists into a single vocabulary entry.

## Growth still increases reads even with one matching file

Option 2, eight buckets, 2,048-row groups, 64 MB target, rare answer fixed:

| Content identities | Appends | Maintained MB | Rare warm ms | Rare HTTP KB | Rare files |
|---|---:|---:|---:|---:|---:|
| 500 | 10 | 22.69 | 2.49 | 63.7 | 1 |
| 1,000 | 10 | 45.57 | 2.74 | 107.5 | 1 |
| 2,000 | 10 | 99.09 | 3.15 | 193.6 | 1 |
| 2,000 | 40 | 101.00 | 3.33 | 193.8 | 1 |

The fourth row holds average batch size at 50 contents. Even one selected file
is not constant read cost: metadata and row-group structure grow. The measured
HTTP bodies include those costs. A content restriction also need not reduce reads:
at 500 contents/small row groups, common-node and scoped-common queries both read
124,866 bytes despite very different answer sizes.

Option 1's top-level term count hides growing nested contents/nodes. Its total
storage and decode work still depend on corpus size and batch count, not just
language vocabulary. All shapes must ultimately return the requested matches;
frequent words cannot have constant cost when returning every matching identity.

## The unresolved file-layout issue

We deliberately lowered the target file size to **1 MB**, with 2,000 contents,
ten appends and eight buckets, to force several files per partition. This is a
stress control, not a suggested production setting.

| Option | Active files after native merge | Files read for fixed rare word | Rare HTTP KB |
|---|---:|---:|---:|
| 1 | 24 | 3 | 363.9 |
| 2 | 32 | 4 | 215.8 |
| 3 | 80 | 10 | 930.5 |

Another native merge call reported no work for every shape and left snapshots
unchanged. The same data at a 64 MB target used eight maintained files and read
one. Sorting each merge group does not establish globally disjoint term ranges
across all files. Therefore selecting a table partition/sort policy and repeatedly
calling this native merge method is **not yet evidence of a scalable lookup path**.

The next architecture test should find and verify a native DuckLake maintenance
policy that bounds this overlap at realistic file sizes, then measure actual remote
requests/bytes as unrelated data and appends increase. This remains lake maintenance;
it does not require LakeDucktor to understand words or combine arrays. Actionable
evidence is recorded in [UPSTREAM.md](../../../UPSTREAM.md).

## Validation and limits

- `make check` passed (backend: 760 tests, 34 skipped; SDK: 24 tests, six
  skipped; package/public/admin checks, tests and builds completed). All five
  additional ICU/parser semantic tests passed; experiment scripts compiled.
- All 30 variants passed full bidirectional multiset equality against the flat
  reference before and after maintenance. All nine complete query answers agreed
  in types, counts and ordered digests across shapes/settings/states. Growth queries
  were compared within the same corpus multiplier.
- Ordinary latency measurements used an initial run and two warm runs. Maintained
  tables also had a reversed query/layout order with two more warm runs. Reported
  medians use two fresh-state or four maintained-state warm runs. Profiling was
  separate from timed runs.
- Byte measurements used new connections to a localhost HTTP range server and
  counted actual successful response bodies, validating the complete result digest.
  DuckDB profile byte counters were not treated as full physical I/O: they often
  reflected mostly footers. HTTP transport/latency is not a production S3 simulation.
- Fresh HTTP probes use closed pre-maintenance metadata copies. Logical time travel
  alone is insufficient to recover the original physical files. Early variants
  without such copies report fresh HTTP bytes as unavailable; maintained measurements
  exist for all 30 variants.
- Host: Apple M1 Pro, 16 GiB RAM, DuckDB 1.5.5, two threads, 2 GB writer / 512 MB
  query limits. Native maintenance used the exact production LakeDucktor image
  digest documented in the [methods](README.md), amd64 Docker emulation, 4 GB engine
  / 6 GB container limits. Disk caches were not flushed. Maintenance times are not
  production throughput estimates.
- The source hashes have a narrow prefix, so known-content pruning is not a broadly
  representative distribution. Source preparation peak RSS was approximately 1.41 GB;
  writer RSS includes a full reference corpus and must not be read as production
  batch working memory. Synthetic clones repeat vocabulary and compression patterns.
- A larger full-set checker initially exhausted its spill allowance. It was changed
  to verify eight disjoint content-hash subsets; this changes only verification,
  not measured storage or queries. Failed and preliminary tokenizer/image runs were
  excluded. Three alternative option-1 projection SQL forms retained whole-postings
  scans and showed no consistent timing improvement; this does not rule out future
  optimizer improvements.

The scripts and reproduction steps are in [README.md](README.md). Complete sanitized
variant measurements are in [scorecard.md](scorecard.md); raw JSON, plans, HTTP
counts and disposable lakes remain in ignored `.artifacts/index-layout/`.
