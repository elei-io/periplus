# Rebuild capacity: a week-scale requirement

> Later launch decision (2026-09-15): the user selected separate Zstd bodies
> and production metadata batching, accepting a possible later body migration.
> [Implementation, adoption and final validation](../../docs/ARCHIVE_ADOPTION.md).
> The measurements and earlier decision gates below are historical; weekly
> billion-document rebuild capacity has not been established.

Measured 2026-09-15 against application source
`19d8d9cad7b453680f58ab4ee147b06465fd0d9c`. The adjacent diagnostic scripts
isolate source reads, projection CPU, and prepared ClickHouse inserts. They do
not change production code or the retained archive.

**The current pipeline has not demonstrated a billion-document rebuild in a
week. Passing archive ingestion at 50/s is insufficient to approve its permanent
physical body layout.** Keep Zstandard and content identities; hold the decision
between individual body files and indexed packs until a storage-cache-independent
read comparison and a faster complete materialization path are proven.

This supersedes the unconditional separate-body recommendation in the earlier
[metadata batching report](METADATA_BATCHING.md). Its 50/s ingestion result
remains valid for its measured duration. These are separate acceptance criteria.

## Required work rate

A billion distinct documents in seven days requires **1,653.44 historical
documents/s**, before allowing time for final verification/publication and
interruptions. A proposed engineering target is **2,000 historical documents/s**
while 50 new captures/s continue arriving. That gives about 5.79 days for the
historical work. Serving and candidate recipes also need live materialization;
any still-maintained previous recipe consumes additional capacity.

Captures and documents must be counted separately: repeated captures can share
a document only when both content and interpretation match. The trials below
deliberately use unique documents. Future workloads need both unique-heavy and
real duplicate distributions, plus explicit accounting for oversized failures.

The 2,000-document sample contains 100,657,594 compressed bytes and 778,271,404
decoded bytes. At the same size distribution, a billion unique bodies would
contain approximately 50.3 TB compressed, requiring **83.2 MB/s of useful raw
reads** to finish in a week. This excludes metadata, retries and material writes.
It is sample arithmetic, not a billion-document storage measurement.

That byte rate is not an obvious impossibility for sequential HDD reads. For
context, Seagate specifies up to 180 MB/s on outer tracks for the ST4000VN008,
one of the recorded drives, in its [IronWolf datasheet](https://www.seagate.com/files/www-content/datasheets/pdfs/ironwolf-18tb-DS1904-20-2111US-en_US.pdf).
This is not a measurement of the mixed-drive RAIDZ2 pool, nor a random-small-file
rate. Do not multiply seven drive specifications and call that archive capacity.

## Source reads: cache changes the answer

`rebuild_reads.py` selects 16,000 unique bodies outside the preceding 12,000-body
ingestion fixture. Four processes with sixteen threads each read through the
real S3 gateway. Every body is fully decompressed and SHA-256/length verified.
There is one GET per body, no HEAD, parsing, journal discovery or source write.
The second pass reads exactly the verified first-pass set.

| Same 16,000 bodies | First pass, cache uncontrolled | Immediate repeat |
| --- | ---: | ---: |
| Wall seconds | 78.09 | 9.97 |
| Verified bodies/s | 204.88 | 1,605.52 |
| Compressed MB/s | 10.30 | 80.70 |
| Aggregate client CPU seconds | 23.93 | 31.80 |
| GET p50 / p95, seconds | 0.245 / 0.846 | 0.024 / 0.051 |
| GETs / failures | 16,000 / 0 | 16,000 / 0 |

The client pod has a four-vCPU quota. The first pass used only about 0.31 CPU
equivalents averaged over its wall time; the repeat used 3.19. NAS telemetry
during the first pass showed substantial disk activity, including intervals of
roughly 600–920 physical read operations/s and only a few MB/s of disk bytes.
The first pass therefore encounters storage waits absent from the repeat. It
does not establish how much latency belongs to platter seeks, filesystem
metadata or the gateway. Telemetry is aggregate, not an exclusive disk trace.

Neither pass is a certified cold-cache ceiling. The full retained compressed
source inventory is only 9.31 GB, smaller than the NAS's approximately 25.5 GB
ARC. Cache state was not dropped or modified on the shared appliance. The
first-pass result warns against extrapolating freshly written/warm-object tests;
it must not be labelled the pool's theoretical maximum either.

Read input: 804,179,949 compressed bytes, 6,060,348,051 decoded bytes, maximum
11,725,815 bytes; no input exceeds 32 MiB. Input fingerprint:
`ee2ad7bdfbd78c8a626b716e1b367746ea2af0c1a18cc5d99b3268196809790b`.
Both passes' sorted capture-ID digest:
`c0dbae014ce22cd47c30588056878f5c29e82530d89a05cda807ed3700b8a0ba`.

## Projection: the parser is already native

The runtime uses **Lexbor 3.1 through Selectolax 0.4.11**, with the complete
ctypes DOM adapter. `dom/lexbor.py` and `dom/nodes.py` are unchanged from the
pre-ClickHouse commit `5e7c572`. This experiment did not replace native parsing
with HTML5lib. HTML5lib's encoding utility remains in the adapter, but the tested
materialization path supplies an already-decoded string to Lexbor.

`rebuild_profile.py` calls the real projection over verified local body fixtures,
removing database lookups and claims only in the diagnostic process. It measures
one compact JSON encoding in addition to projection. This is a CPU floor, not
an executable production rebuild that can safely omit those controls.

| Homelab, same 2,000 documents | CPU seconds |
| --- | ---: |
| Native Lexbor construction, nested within DOM work | 3.01 |
| Complete DOM traversal and Python record construction, including Lexbor | 39.21 |
| HTML content model construction | 11.15 |
| Link extraction | 7.29 |
| Canonical output encoding/hashing | 8.43 |
| One final compact wire encoding | 6.46 |
| Complete projection plus one wire encoding | **84.64** |

The named stages do not cover all work; model dumping, raw verification,
intermediate object construction and other work remain. Native parse is nested
and must not be added again. Wall time was 84.68 seconds, **23.62 documents/s**.
The MacBook control used 93.66 CPU seconds and 99.11 wall seconds, with identical
output identity/digest fingerprints. Profiler-enabled timings are kept separate.

A 200-document local cProfile attributes large costs to Python traversal,
ctypes string extraction, node/dataclass creation, JSON encoding, Pydantic and
URL normalization. Optimizing the native parser alone cannot remove those costs.
The retained complete adapter intentionally includes template fragments,
namespaces, comments and document-wide positions: a faster path must preserve
these semantics, not achieve speed by silently dropping them.

At 42.3 ms CPU/document, projection alone would require roughly **70 concurrent
single-worker CPU equivalents** at the week-scale rate. The lab inventory has
three 12-core/24-thread physical hosts; VM vCPUs and SMT threads must not be
treated as additional full physical cores. This arithmetic is not a scaling
benchmark, but it rules out assuming the current Python path is comfortably
within the compute budget. The full worker's earlier 120–147 CPU seconds per
2,000 documents includes still more overhead and excludes database CPU.

The sample has 3,310,305 elements. Its compact output
is 1,839,707,223 bytes: **18.3 times the compressed raw input**, about 920 KB per
document. At the week-scale rate, uncompressed JSON traffic would be 1.52 GB/s.

## ClickHouse: separate the write floor from the full worker

`rebuild_bulk.py prepare` projects the same 2,000 documents, prepares 234 bounded
blocks, and verifies each Zstd block decodes to exactly its compact JSON bytes.
Blocks target 8 MiB, with larger individual rows bounded to 32 MiB. Zstd level 1
reduces 1,839,707,223 bytes to **221,789,809 bytes**, using 1.41 CPU seconds for
compression. Preparation is outside the following insertion interval.

Four HTTP lanes insert those pre-encoded blocks into fresh UUID-named databases
using the production table schema and typed `input()` conversion. ClickHouse
26.8.2.7 has three vCPUs and 4 GiB RAM, on k3s-01's local storage; the client
runs on k3s-03. Inserts are synchronous. There is no source S3 traffic.

| Trial order | Encoding | Seconds | Documents/s | Insert-query CPU seconds |
| --- | --- | ---: | ---: | ---: |
| 1 | Compact JSON | 5.87 | 340.46 | 9.16 |
| 2 | Same JSON, Zstd transport | 6.03 | 331.81 | 9.19 |
| 3 | Same JSON, Zstd transport | 5.99 | 333.63 | 9.21 |
| 4 | Compact JSON | 6.25 | 320.03 | 9.50 |

All trials have exactly 2,000 capture and document identities and the expected
stored output digests. Compression cuts transport bytes by **8.3×**, but does
not establish an insert-speed difference in these short trials. It is useful
network headroom, not a demonstrated end-to-end speedup. The current runtime
does not enable it. ClickHouse documents [HTTP compression](https://github.com/ClickHouse/clickhouse-docs/blob/main/docs/integrations/interfaces/http.md)
and [Native block insertion](https://github.com/ClickHouse/clickhouse-docs/blob/main/docs/best-practices/selecting_an_insert_strategy.md);
Native producer/transport performance has not been measured here.

This is a prepared-insert floor: it excludes source reads, parsing, encoding,
compression, claims, restart recovery and verification time. Insert-query CPU
excludes background merge CPU. Between 20 and 27 active parts remained when
sampled; merges were not drained before the timing finished. A 2,000-document
empty-database run cannot certify long-running merge or index-building capacity.

Together with the earlier **63.80/s full materializer** result, the tests expose
substantial upstream/coordination overhead. They do not prove a 330/s production
rebuild or that allocating more ClickHouse CPUs scales linearly.

## What must change, and the decision gate

1. **Prove sequential archive reads without relying on RAM cache.** Compare
   separate Zstd bodies and bounded indexed packs containing the identical Zstd
   frames. Measure a direct sequential-storage control, gateway throughput,
   object operations, CPU, disk latency/utilization and verified useful MB/s.
   Use an isolated fixture larger than relevant caches, or a supported cache
   bypass scoped only to a disposable dataset. Do not flush production caches
   or change retained-dataset properties. The existing 1,000-capture packed
   results are warm tests and cannot answer this question.
2. **Reduce projection CPU while preserving output.** First remove repeated
   representations/encodings and inspect bulk native DOM extraction, retaining
   the complete parser contract. Compare every canonical field, identities,
   nulls and failure outcomes; stored digest equality alone is not sufficient
   proof for an algorithm rewrite. The native parser itself is not the main
   measured CPU expense. A narrow native extraction boundary is a candidate,
   not an already-implemented speedup or a reason to add a DuckDB extension.
3. **Batch the existing correctness protocol.** Replace per-document existing
   output queries and repeated serialization with bounded batch lookups and
   prepared blocks. Retain exact claims, retirement checks, durable checkpoints,
   conflict detection and uncertain-write recovery. Reading by physical pack
   order must still resume from bounded archive positions and never depend on
   a permanent Postgres corpus index. Reuse the build lifecycle, not a second
   correctness system for backfills. Measure compressed/Native inserts and
   steady-state merge work with the real row shape.
4. **Run the assembled service with an aged namespace.** Keep ingestion at 50/s
   while historical throughput approaches 2,000/s, or report its measured
   limit and the resulting supported corpus size. Include live materialization
   for serving/candidate recipes, backpressure, ordinary queries, checkpoints,
   restart/retry, tombstones, final verification and atomic publication. Measure
   read amplification, all CPU, network, merge debt, memory, and temporary space
   for overlapping material targets. Published/reconciled progress is the rate;
   parser calls or queued inserts are not completion.

A short isolated run can establish stage limits and catch software problems;
it cannot certify a week-long billion-document rebuild. The full gate needs
stable throughput and bounded backlog/merge debt over a growing dataset, a
representative size/DOM distribution, and a stated extrapolation uncertainty.
If measured storage throughput is below the target but near the isolated
sequential control, report that hardware limit rather than adding complexity
to hide it. This experiment has **not** reached that diagnosis.

If separate files fail that read gate and packs pass it, packing becomes a
requirement for the archive layout. It still needs reconstructible bounded
locators, individual downloads, short durable publication latency, deduplication
under retries, and reader-safe reclamation. Packing does not require gzip or
WARC and does not change the SHA-256 of the decoded content. Those protocol
proofs are not supplied by a fast sequential scan alone.

## Reproduction and evidence

Use the locked Periplus Python environment. Reuse the isolated diagnostic
manifests described in [METADATA_BATCHING.md](METADATA_BATCHING.md), copying
current source to `/work/project/packages/periplus/src/periplus`, scripts to
`/work/archive`, and dependencies/inventory to `/work`. No production deployment
is edited. The projection fixture cache contains the same selected 2,000 bodies.

```sh
PYTHONPATH=/work/project/packages/periplus/src:/work python /work/archive/rebuild_reads.py \
  --inventory /work/inventory.jsonl.gz --output /work/reads.jsonl
PYTHONPATH=/work/project/packages/periplus/src:/work python /work/archive/rebuild_profile.py \
  --inventory /work/inventory.jsonl.gz --cache /work/cache \
  --output /work/projection-lab.jsonl
PYTHONPATH=/work/project/packages/periplus/src:/work python /work/archive/rebuild_bulk.py prepare \
  --inventory /work/inventory.jsonl.gz --cache /work/cache \
  --output /work/bulk-prepare.jsonl --blocks /work/bulk-blocks
PYTHONPATH=/work/project/packages/periplus/src:/work python /work/archive/rebuild_bulk.py insert \
  --output /work/bulk-insert.jsonl --blocks /work/bulk-blocks
```

`prepare` requires a new output directory. `insert` uses only the explicitly
named disposable database service, creates new material targets, verifies rows,
and drops them synchronously. All trial data, URLs and raw logs remain ignored
under `.artifacts/rebuild-capacity/`. Source-read/projection/bulk trials run
separately; the projection's local fixtures do not compete for NAS reads.

Projection/bulk input fingerprint:
`8ca920fad25fad9513a282b118b3039cf571f62e0f5dfdbd682a18544f7110dc`.
Expected capture identity/output-digest fingerprint:
`ac56ad99937acdf00cedd5082b8ef18f9924809d3c1eaa197efb0087e27db2eb`.
Expected document identity/output-digest fingerprint:
`3bcbbc631d290babad57a1cc0faacc732a4d072758a0a11ba369a6e9024ac11d`.
These match the earlier full successful materializer trials.

`make check` passed after adding the diagnostic scripts. Targeted Ruff syntax
and undefined-name checks pass. No production code or source objects were
changed. All diagnostic material databases were dropped; the three temporary
pods, service and two network policies were removed. A final resource query
returned no remaining diagnostic resources. Cleanup is recorded with the local
run evidence.

## Managed ClickHouse deployment — 2026-09-15 follow-up

After the size-limit fix (`38e5486`), candidate
`2a70e436-37aa-42a5-932e-3caa2654d889` reached 76,096 committed observations
without failed batches at 15:22:26 UTC, after starting at 15:08:33 UTC. Ten
workers rebuild while two preserve the old serving recipe. Early sustained
throughput is approximately 90–100 observations/s; an earlier target count
showed 38,411 unique documents at 48,184 observations, implying roughly 75–80
unique documents/s during that interval. This is partial-run evidence, not a
finished-rebuild result or a billion-document forecast.

A bounded diagnostic processed the first 512 observations from journal shard
zero (406 HTML captures, 402 distinct documents) through the deployed
`apply_batch`/`materialize_many` code into a disposable ClickHouse database.
It retained actual Postgres write protection and object checks, and excluded
NATS scheduling and final build-checkpoint transactions. Source reads were warm:
the ongoing rebuild had already read this early range. The database and
Kubernetes diagnostic resources were removed afterwards.

| Measured stage | Calls | Wall seconds |
| --- | ---: | ---: |
| Acquire exact write claims | 453 | 8.68 |
| Release exact write claims | 453 | 9.43 |
| DOM conversion, text models, links, canonical encoding | — | 16.57 |
| ClickHouse SELECT requests | 2,025 | 7.43 |
| Raw/metadata reads | 420 | 1.25 |
| Object existence checks | 2,092 | 3.26 |
| ClickHouse inserts | 117 | 2.19 |
| Remaining work | — | approximately 4.5 |

Total wall time was 53.35 seconds, process CPU 27.30 seconds and peak RSS
321.9 MiB. Claim timing excludes protected body work. Postgres uses local-path
volumes, `synchronous_commit=on`, `fsync=on` and an ANY 1 synchronous replica
requirement. These measurements do not isolate network versus WAL flush/replica
latency. Batch the correctness protocol rather than weakening durability.

Concurrent production snapshots showed workers around 0.4–0.6 CPU each,
ClickHouse around three CPU cores and 3 GiB, insert p95 around 35 ms, and no
active merge at the sampled instant. Public count SQL remained around 30 ms.
These small-corpus observations do not certify large public-query performance.

The immediate candidates remain bounded batch claims, bounded batch existing-row
lookups, fewer repeated retirement checks with equivalent deletion fences, and
less Python representation/encoding work. Even removing all measured claim and
SELECT time would yield only about a 1.9x diagnostic speedup; it would not close
the roughly 20x gap to a billion unique documents per week. Native projection,
transport efficiency, scheduling and cache-independent source-read capacity
still need separate proof. This run has not established a hardware ceiling.
