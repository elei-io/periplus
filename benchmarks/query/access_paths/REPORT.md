# ClickHouse access-path experiment — 16 September 2026

**Element rows are a serious candidate, and native inverted indexes are useful.**
The experiment found efficient positive access paths over 31 million real elements.
It also found that the current public visibility filters, text reconstruction and
some optimizer transformations can defeat those paths. This is evidence for a
better next design, not a certification of billion-document operation.

## What was actually compared

The source was frozen build `material_2a70e43637aa42a5932e3caa2654d889`.
The isolated copy contains 20,000 distinct documents, 20,868 complete captures,
and 30,946,741 elements. The cumulative cohorts contain 200, 2,000 and 20,000
real documents. No repeated synthetic page was counted as new corpus.

The IC3 positive anchor is pinned first; the other documents are the first 19,999
binary document hashes in order. This is a varied retained sample, not a rigorous
random sample of the web. In particular the anchor hash is above the sampled
hash prefix, making its document-ID lookup unusually easy. A separate experiment
uses a midpoint key and overlapping parts to test that weakness.

Code is based on repository revision `ea0a8d6`. ClickHouse is **26.8.2.7**, using the
existing NAS/S3 `corpus` policy and bounded NVMe filesystem cache. Its pod requests
10 CPUs and has 28 GiB memory; the three Kubernetes nodes each expose 19 CPUs
and about 44.6 GiB allocatable memory. The benchmark used its own two-thread
workload. Probe workers had **one CPU and 1 GiB memory**. Shared production/staging
work and background merges continued; timings are individual observations,
not p95s or an idle-machine capacity limit.

Query limits were 512 MiB memory, 1 GiB logical reads, 20 seconds and 1,001 result
rows. Queries used the native client against private relations, not API latency.
Private public-shaped views preserve the relevant field expressions and capture
membership filters. The original selected-links business SQL is retained, with
only private namespaces and an explicit URL-literal update to the current
normalized `capture.url` contract (`https://www.ic3.gov/`).

The controls preserve the document/array representation but add benchmark rank,
public-form hexadecimal identities and a class helper; they are not byte-for-byte
copies of production tables. Direct indexed-document class cases use a document
token candidate filter followed by an exact element recheck. Public-shaped class
cases keep the original attribute predicate and do not receive that extra gate.
A successful private-table access path is not counted as a public-view fix.

| Candidate | Physical representation | Purpose |
| --- | --- | --- |
| `docs_plain` | One canonical text and element array per document | Existing layout control |
| `docs_indexed`, `docs_indexed_packed` | Same row, native document-word and class-token indexes | Test indexes before row expansion; compare part file packing |
| `elements_plain` | One real row per element; attributes, direct text, spans | Flat control |
| `elements_compact` | Flat rows, token indexes and tag/id projections | Initial indexed element design |
| `elements_lean` | Flat rows, scalar id projection, class expression, aggregate document counts | Remove accidental projection duplication |
| `elements_full`, `elements_full_lean` | Flat rows plus complete subtree text and its index | Preserve full public element semantics without a text join |
| Capture variants | Same captures, no projection / offset projection / covering projection / exact URL index | URL selection and selected-page links |
| JSON-LD variants | Script rows with raw JSON, extracted types/name, indexes/projections | Positive Product extraction |
| `attributes_probe` | Exact Map keys/values indexes over real element identities | Arbitrary attributes, including wrong-key rejection |
| `documents_text` | Canonical document text, count and text index, without the element array | Avoid storing the array again beside an element table |

`text_direct` and subtree `text` have different meanings. Their searches are
reported separately. Text tokenization is explicit `lower(...)` plus
`splitByNonAlpha`; this is not a linguistic English analyzer. Class tokens use
whole-token semantics, preserving punctuation such as hyphens.

## The main read result

The same three `hp-icon` elements remained the answer while unrelated documents
increased by 100×. Both positive direct-text rows also remained fixed.

| Rare class lookup | 200 documents | 2,000 documents | 20,000 documents |
| --- | ---: | ---: | ---: |
| Unindexed element rows, logical read | 11.79 MiB | 119.7 MiB | Exceeded 1 GiB |
| Indexed element rows, logical read | 0.351 MiB | 0.160 MiB | 0.160 MiB |
| Indexed result count | 3 | 3 | 3 |

At 20,000 documents the initial indexed element run took **29 ms**, read 2,436
physical rows, and used about 3 MiB recorded memory. The rare direct-text search
read the same 0.160 MiB in 31 ms; its unindexed control exceeded the scan budget.
Document-level text indexing also worked: the rare word combination returned
two documents while reading about 1 KiB; the document-array scan failed its
memory budget. Indexing the search field is useful even when no element rows
are needed.

Some answers deliberately grow: the same-element `usa-header` /
`usa-header--basic` intersection returns **1 → 4 → 39** elements. The rare
canonical-document text combination returns **1 → 2 → 2** documents; subtree
text returns **15 → 17 → 17** elements. These are not presented as fixed-answer
experiments. A class pair present on different IC3 elements returns zero,
checking that document-level token presence is not mistaken for a same-element
match.

Common words remain a different workload. There are **530,654** direct-text
rows containing `the` in the largest cohort. A native indexed count can use
posting information without returning those rows. Returning the first 1,000
full direct-text results from the full element table took 13 ms and read 18.9
MiB in the measured repeated run. Asking to return them all correctly hit the
result-size limit. Fast indexed counts do not imply free bulk exports.

## Reusable units really do help

Distinct class tokens grew **8.5×** while document count grew **100×**. At the
largest cohort, each distinct class token occurred about 218 times on average.
That is the useful reuse the proposed classname vocabulary was looking for.

| Unit | Distinct at 200 docs | Distinct at 2,000 | Distinct at 20,000 | Occurrences at 20,000 |
| --- | ---: | ---: | ---: | ---: |
| Class tokens | 23,602 | 72,355 | 200,699 | 43,777,734 |
| Whole class strings/combinations | 19,233 | 63,653 | 200,135 | 17,087,515 |
| Attribute names | 2,108 | 5,633 | 11,928 | 61,109,737 |
| Tags, including custom tags | 232 | 812 | 1,572 | 30,946,741 |
| Canonical-text tokens, approximate distinct count | 87,301 | 360,140 | 1,555,572 | 382,104,450 |

Whole class strings preserve order and spacing; they are not canonicalized sets.
The canonical-text tokens include scripts/styles and are not a count of English
words. Vocabulary growth is slower here, but neither vocabulary nor posting
occurrence count is bounded independently of corpus size. Native indexes already
exploit much of this reuse without introducing our own global dictionary service.

## Element rows versus document arrays

The full element table answered the current public-shaped ten-row preview in
**29 ms**, reading 11.6 MiB with 162 MiB recorded memory. The document-array
variants and compact-element/text-join view still failed the 512 MiB budget.
A separate outlier document with 2.2 million canonical text characters also
returned a correct ten-row preview from stored full element text.

The compact rows are attractive for storage and targeted extraction. A
selected-document heading query works: the predicate restricts the text join
before expensive reconstruction. An unrestricted preview is a different plan
and does not inherit that guarantee. We cannot recommend the compact join as a
transparent replacement for arbitrary public element SQL yet.

**Public `COUNT(*)` still fails the 1 GiB scan budget at the largest cohort,
including on full element rows.** Direct table count is metadata-cheap, but the
capture-membership guard makes the public query inspect element identities.
An aggregate projection grouped by document worked with a literal document ID,
but the optimizer did not use it for the membership subquery. This is an
unresolved publication/schema and optimizer issue, not a reason to delete the
visibility guard without proving the replacement's correctness.

## Native indexes and projections: useful, with specific traps

- Arbitrary Map attributes do work. Looking for the exact SVG `viewBox` value
  read 959 rows / 0.066 MiB and returned one element at the largest cohort.
  With skipping indexes disabled, the same answer required 30,946,741 rows and
  about 1.57 seconds. Asking for the value under the wrong key returned zero.
  Key/value postings find candidates; the exact Map predicate still matters.
- An exact URL keyword index reduced latest-capture lookup from 21,082 physical
  rows / 2.04 MiB to 428 rows / 0.035 MiB. The offset projection was not selected
  for that exact lookup; the covering projection reduced work but copied links.
  All successful selected-links variants retained the independent 50-row answer.
  Their public-shaped joins still do more work than the underlying URL lookup.
- For JSON-LD, a small exact-name index plus a type index kept the positive
  Product lookup at 179 physical rows / 450 KiB through all three cohort sizes.
  The largest repeated run took 4 ms. The earlier name projection did not
  materially prune this query. Removing the unused projection and broad raw-JSON
  text index also made this focused table smaller: 7.46 MB versus 13.47 MB.
- A projection on `attributes['id'] AS id` unexpectedly carried the entire Map.
  At 20,000 documents its projection occupied about 1.5 GB. A materialized scalar
  `element_id` with an offset projection reduced that to about 270 MB. Merely
  calling something a lightweight projection does not make its dependencies
  lightweight.
- Map subcolumn optimization changed the class expression so it missed the
  inverted index. The full element table's default class lookup scanned all
  31 million rows. The **same SQL** with native
  `optimize_functions_to_subcolumns=0` used the index and read 455 rows / 30 KiB
  in 3 ms after merges. This is a concrete optimizer interaction, not evidence
  that class indexing fundamentally cannot work. Do not set this globally
  without checking its effect on other Map queries.
- An ordinary projection cannot turn each array element into a separately
  indexed row: the installed server rejected `arrayJoin` in its sorting key.
  Flat element identity requires physical rows or a row-expanding materialization.

These findings use documented ClickHouse features. Text indexes became generally
available in 26.2; `tokenizer=array` supplies whole-value semantics. The newest
documentation also lists features absent from the installed tokenizer catalogue,
so documentation alone was not treated as proof of availability.
[Text-index documentation](https://clickhouse.com/docs/reference/engines/table-engines/mergetree-family/textindexes).
Offset and covering projections were tested separately because they have different
storage and read behavior.
[Projection documentation](https://clickhouse.com/docs/concepts/features/projections/projections).

## The remaining corpus-growth axis: storage parts

The first 20,000-document class run consulted **181 part indexes**, including
180 negative token-cache hits, even though it read only one result granule.
Merges later reduced that table to nine parts. Low payload reads alone would
have hidden this overhead.

A separate test held the same 20,000 real document keys and one midpoint-key
answer fixed, while distributing the keys across overlapping parts. The cache
bypass disables filesystem and text-index caches for the query; NAS and OS
caches remain uncontrolled. These are repeated server measurements, not cold
HDD timings.

| Layout | Parts considered | Data rows read | Warm ms | Bypass ms | Bypass S3 GETs |
| --- | ---: | ---: | ---: | ---: | ---: |
| Plain, 2 parts | 2 | 20,000 | 2 | 5 | 2 |
| Plain, 100 parts | 100 | 20,000 | 9 | 65 | 100 |
| Bloom, 2 parts | 2 | 10,000 | 2 | 6 | 3 |
| Bloom, 20 parts | 20 | 1,000 | 3 | 10 | 21 |
| Bloom, 100 parts | 100 | 200 | 8 | 68 | 101 |
| Text index, 2 parts | 2 | 10,000 | 3 | 13 | 5 |
| Text index, 20 parts | 20 | 1,000 | 4 | 22 | 41 |
| Text index, 100 parts | 100 | 200 | 12 | 78 | 201 |
| 64 hash partitions, one part each | 1 of 64 | 293 | 1 | 3 | 1 |

The fixed hash partition was pruned natively from the identity predicate. This
is a useful direction for known-document access, not a recommendation to give
every table 64 partitions. Global class/text queries have no known document
bucket to prune. A fixed partition count also does not cap parts within each
partition as data grows.

The supported hypothesis is therefore **work proportional to relevant postings,
fetched granules, results and consulted parts**, rather than every element in
the corpus. It is not constant total work independent of corpus size. Mature
part sizes, merge capacity, pruning and index-cache residency belong in the
scale budget alongside row counts.

## Storage cost

Active-part snapshots below include indexes and projections. Decimal GB/MB are
used here. Component sizes must not be added again to table totals. Background
merges continued, so these are measured snapshots rather than perfectly matched
single-part compression tests.

| Table, largest cohort | Total stored |
| --- | ---: |
| Plain document array | 2.126 GB |
| Indexed document array | 2.287 GB |
| Canonical document text only, with word index | 0.698 GB |
| Plain compact element rows | 1.940 GB |
| Initial indexed elements with expensive id projection | 4.175 GB |
| Refined compact element rows, indexes/projections | 2.835 GB |
| Refined full-text element rows, indexes/projections | 5.256 GB |
| Captures, no alternate access path | 55.00 MB |
| Captures, exact URL index | 55.60 MB |
| Captures, offset projection | 56.24 MB |
| Captures, covering projection including links | 107.62 MB |
| Focused JSON-LD type/name indexes | 7.46 MB |

The full element table's class and direct-text indexes occupy about 456 MB;
the subtree-text index adds about 983 MB. Its two alternate-order projections
add about 545 MB. The arbitrary-attribute probe has about 0.91 GB of key/value
index data above its 0.83 GB base columns. Indexing every arbitrary value is a
much larger storage choice than indexing URL or JSON name fields.

Combining the measured canonical-text-only table, full element table, keyword
captures and focused JSON-LD table costs **about 6.02 GB for 20,000 documents**.
Straight multiplication gives about **301 TB for one billion** with this sample
mix. That is a sensitivity calculation, not capacity certification: it excludes
raw archives, extra attribute indexes, replicas, retained builds, merge headroom,
caches and backups. The compact-element equivalent is about 3.60 GB here but
does not solve unrestricted full-text element reads. Keeping a second copy of
the entire element array is unnecessary in either proposed combination.

## Materialization cost and correctness

Expanding complete subtree text inside one `INSERT SELECT` failed even at 200
documents: a single allocation of about 2.66 GiB would have exceeded the 2 GiB
write limit. Smaller SQL blocks and LowCardinality did not solve that path.
A bounded RowBinary writer successfully produced full element rows instead.
It slices character offsets before UTF-8 encoding and never truncates text.

For the additional 18,000 documents, the full element writer produced 27,975,421
rows and **26.61 GB uncompressed wire data** in **1,207 seconds**, of which 1,015
seconds were spent awaiting inserts. Python CPU was 223 seconds, including
95 seconds encoding rows. This reads already-parsed source documents; it is
not an archive rebuild result.

| Additional 18,000 documents, foreground writes | Server seconds | Server CPU seconds | Reported S3 PUTs |
| --- | ---: | ---: | ---: |
| Plain document array | 467 | 58 | 21,580 |
| Indexed document array with Compact-part threshold | 256 | 60 | 7,611 |
| Plain compact elements | 336 | 50 | 16,874 |
| Initial indexed elements | 422 | 91 | 30,088 |
| Refined compact elements | 379 | 75 | 15,158 |
| Refined full elements, bounded writer | 1,012 | 173 | 15,635 |

The native `INSERT SELECT` rows include source reads in their server time. The
full writer's server row sums its inserts; its source streaming and client time
are separately included in the 1,207-second wall measurement above. These are
not equal end-to-end pipelines. Plain and packed document variants also differ
in packing, so that row must not be read as "adding an index makes writes faster."

A separate pilot fetched and verified **100 actual retained Zstd bodies**, used
the existing Lexbor parser, and wrote **158,520** full element rows. With four
bounded fetches and one CPU it took **9.35 seconds**, including 5.22 seconds
awaiting two inserts; process CPU was 3.19 seconds. Its 150 MB wire payload was
uncompressed. This is a raw-to-private-table pilot, excluding durable delivery,
receipts, retries and publication, not a production throughput promise.

A read-only parser probe independently checked 101 bodies, including the large
outlier. Canonical text hashes and node counts matched all 101 stored documents.
For the first 100, parser CPU was 2.36 seconds; sequential body fetch/verification
was 7.66 seconds. The current parser is already native-backed. These observations
do not support treating a language rewrite as the first necessary fix.

Part file packing mattered. For the same indexed document layout, changing the
threshold for Wide parts to 256 MiB reduced S3 PUTs for the additional 1,800
rows from **2,464 to 758**, and insert time from **43.5 to 29.7 seconds**.
This packs columns into Compact parts; it does not eliminate the need to merge
parts. The combined refined element design plus 96 MiB writer batches was also
much faster than the original projection-heavy design with 32 MiB batches,
but that combined comparison does not isolate one setting's effect.

We observed substantial background merge reads as well as foreground insert
writes. Merge duration sums are overlapping task time, not elapsed rebuild time.
Remote requests and waiting were prominent; these measurements do not separate
HDD service time from network, object-service latency and scheduling well enough
to call the disks the bottleneck.

For example, full-element background merges read **81.0 GB of logical input**
and produced **13.5 GB of part output**, compared with 5.26 GB active table size.
They used 204 CPU seconds and peaked at about 1.21 GiB recorded memory, with no
logged merge errors. Object-operation counters are ClickHouse-reported events;
`S3PutObject` alone omits multipart activity and must not be interpreted as all
storage operations. Per-query profiles and merge component counters are retained.
[Part-log documentation](https://clickhouse.com/docs/reference/system-tables/part_log).

A billion documents in seven days requires **1,653 documents/second** sustained.
Neither the single-worker pilot nor this two-thread query campaign demonstrates
that. Full subtree text repeats descendant text in ancestors; within a pathological
deep document that expansion can grow much faster than document size. Compression
reduces disk cost but does not eliminate all encoding, network and indexing work.

## What the next schema should pursue

Use separate physical identities for the things people retrieve:

1. A document row for canonical text, identity and document-level text search.
2. An element row keyed by `(document_id, node_index)`, with structure, attributes,
   direct text, spans and native class/text access paths. Store complete subtree
   text only with an explicit decision about its measured write/storage cost;
   it is the tested option that preserves unrestricted public full-text reads.
3. Capture rows for observed URLs/time/links, with a native exact-URL access path.
4. Structured-data rows for extracted JSON objects and useful typed fields.
   The measured prototype handles valid top-level JSON-LD scripts and
   `@type`/`name`; it does not implement context expansion or complete `@graph`
   traversal. JSON-LD vocabulary does not make arbitrary values bounded.

Keep compact reusable vocabulary in native indexes and LowCardinality columns
where appropriate. We do not need a custom global classname service, token-ID
registry or Python SQL-rewrite layer to get the measured gains. Before installing
a new runtime schema, fix visibility/publication access, verify native expression
matching for the actual public SQL, and budget parts and merge work alongside
payload storage. Projections should earn their place through selected plans and
measured bytes, not be added to every possible search angle.

For the next write-path experiment, test larger batches and an NVMe hot volume
that merges parts before moving them to the NAS, alongside the current direct
S3 path. ClickHouse supports moving data between configured volumes; the proposed
throughput benefit here remains unmeasured. This is a more directly motivated
next test than assuming a new parser language will remove object-operation waits.
[Native storage-volume moves](https://clickhouse.com/docs/concepts/features/operations/delete/ttl#implementing-a-hotwarmcold-architecture).

The implementation boundary remains unchanged: raw archive is authoritative
for retained evidence, Postgres owns operations/publication, NATS delivers work,
and ClickHouse stores reconstructible query material.

## Verification and limitations

The versioned [runner and reproduction instructions](README.md) preserve SQL,
query IDs, plans, errors, storage components and physical metrics. Independent
oracles verify IC3 classes, headings and selected links, and a real Product
JSON-LD record. Equivalence checks preserve values, order and duplicates; failed
queries are not treated as empty answers. Unordered previews are checked against
their source elements. Node-identity checks compare every sampled document in
each full-size element variant, and selected documents receive full-field checks.
The completed checks found zero node-identity mismatches across four 20,000-
document element variants and the 2,000-document original full-text variant;
78 equivalence groups and 340 preview rows passed. The raw pilot contains exactly
158,520 unique element identities. Ten focused runner/wire tests and the repository's
`make check` passed (environment-dependent tests retain their normal skips).

Classification: giant-array/text reconstruction is **both layout and evaluation
behavior**; Map-expression/index matching is **optimizer behavior**; broad public
membership access is **schema/publication design with an optimizer limitation**;
part fanout is **physical layout/maintenance**. Result-size rejection is expected
admission behavior. Unattributed disk/network/scheduler waits remain unclassified.

The scope is access paths, not complete search semantics or a deployment. No
concurrency SLO, failure recovery under production queues, high availability,
one-billion-document capacity or seven-day rebuild has been certified. Existing
business cases that need broader interfaces, language-aware search or graph
semantics remain future work. The experiment's source corpus and raw archive
were left unchanged. The temporary database, workload and probe pod were removed
and their absence verified after collecting the local evidence.
