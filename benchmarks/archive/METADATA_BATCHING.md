# Metadata-only batching: new-body ingestion and material capacity

> Later launch decision (2026-09-15): the user selected separate Zstd bodies
> and production metadata batching, accepting a possible later body migration.
> [Implementation, adoption and final validation](../../docs/ARCHIVE_ADOPTION.md).
> The measurements and earlier decision gates below are historical; weekly
> billion-document rebuild capacity has not been established.

This experiment keeps separate, content-addressed Zstandard HTML objects. It
changes only the experimental metadata publication format. Production code and
the retained corpus are not changed.

Measured on 2026-09-15 against application source
`19d8d9cad7b453680f58ab4ee147b06465fd0d9c`, with the benchmark-only controls
described below. Diagnostic image digests and resource quotas are pinned in the
adjacent Kubernetes manifests.

## Decision

The newer [week-scale rebuild requirement and phase measurements](REBUILD_CAPACITY.md)
supersede this report's unconditional separate-body layout recommendation.
Metadata batching passed the short 50/s ingestion test; physical body layout
sign-off now also requires cache-independent archive reads and faster full
materialization. Neither has yet demonstrated a billion documents in a week.

The current archive's roughly 17/s result is **not the demonstrated hardware
limit**. Metadata batching met the 50/s offered rate with a short final drain.
Increasing upload concurrency substantially improved the 100/s headroom trial.
Separately, eight materializer processes completed the full sample at 63.80/s
after a narrowly scoped serialization correction, with matching output at every
worker count. The remaining work is production software integration and longer
combined validation, not an unexplained claim that the HDDs cannot do it.

## Results at 50 captures/second

Both final variants received the same 6,000 unique bodies over 120 seconds.

| Measurement | Current archive | Metadata batches |
| --- | ---: | ---: |
| Verified captures / genuinely new bodies | 6,000 / 6,000 | 6,000 / 6,000 |
| Completion rate including final drain | 17.36/s | 49.46/s |
| Backlog when arrivals stopped | 3,920 | 184 |
| Time to drain after arrivals stopped | 225.70 s | 1.32 s |
| Arrival-to-publication p50 | 115.04 s | 2.07 s |
| Arrival-to-publication p95 | 214.34 s | 3.36 s |
| Arrival-to-publication p99 | 222.85 s | 4.28 s |
| Actual S3 HTTP attempts | 161,467 | 24,656 |
| HTTP attempts per capture | 26.91 | 4.11 |
| Final objects | 24,000 | 6,108 |
| Logical stored bytes | 316,503,068 | 312,620,740 |
| Metadata replay from a fresh reader | 59.07 s | 0.47 s |
| Process CPU seconds | 237.48 | 45.04 |
| Failed captures | 0 | 0 |

Batching kept up with the offered rate; a nonzero in-flight backlog is expected
when publication takes a few seconds. The current layout accumulated work
throughout the arrival window. The batched layout saved only about 1.2% in logical
stored bytes here: its main benefit is fewer object operations, not body storage.

At the same 64-lane budget, batching did **not** keep up with 100/s: 12,000 unique
captures completed at 67.87/s including drain, with 3,922 outstanding at the end
of arrivals, a 56.81-second drain and 55.01-second p95 publication latency. All
12,000 bodies were newly uploaded and all captures/bodies verified successfully.

The current format at 100/s completed the same 12,000 captures at 17.16/s,
with 9,857 outstanding at the end of arrivals and a 579.17-second drain.
Publication p95 was 549.55 seconds. All captures and bodies passed verification;
the final 48,000 objects occupied 627,774,173 logical bytes. Fresh-reader metadata
replay took 112.99 seconds. The destination was then completely cleaned.

### Higher-concurrency headroom control

At 100/s with 128 lanes, all 12,000 genuinely new captures completed in
125.48 seconds, including a 5.49-second final drain (95.63/s over that entire
interval). Arrival-to-publication latency was 3.46 seconds p50, 7.24 seconds p95
and 8.72 seconds p99. The arrival window ended with 545 captures outstanding,
versus 3,922 with 64 lanes. There were no failures; every body and capture
verified, and the destination was cleaned.

This demonstrates much better short-run 100/s headroom without more hardware.
It is not an all-day sustained-rate claim: the trial lasts two minutes and the
queue varies during it. The result also refutes treating the 64-lane 67.87/s
measurement as the physical hardware ceiling.

The 128-lane trial made 49,416 HTTP attempts (4.118 per capture), retained
12,188 objects / 619,993,882 logical bytes, and used 102.99 process CPU seconds
(0.82 CPU cores on average). Metadata replay took 6.25 seconds. PUTs accounted
for 92.35% of summed object-call time, with 536 ms median and 1.16 s p95 latency.

### Existing-format software control

Sharing the journal-head cache and serializing in-process appends per shard
eliminated the extra journal PUT attempts. For 6,000 captures at 50/s it made
24,000 PUTs, 30,000 GETs and 29,984 HEADs: 14.00 HTTP attempts per capture,
compared with 26.91 for the baseline. However, completion improved only from
17.36/s to 18.94/s. It ended arrivals with 3,766 outstanding and drained for
196.72 seconds; p95 publication latency was 187.06 seconds, with no failures.

This control shows that redundant discovery and contention are real software
costs, but eliminating them does not reach the target. Four separate PUTs per
new capture remain, at a 494 ms median in this trial. Batching removes most of
the per-capture metadata writes instead of only coordinating them more cheaply.
These are single-process coordination results, not a tested multi-replica
ownership scheme.
All 6,000 captures and bodies verified; metadata replay took 48.66 seconds, and
all 24,000 destination objects were removed afterward.

## Where the time goes

At 50/s, batched PUT latency was 504 ms median and 897 ms p95. GET/HEAD medians
were approximately 1.5 ms. PUTs accounted for 97.6% of summed object-call time.
At 100/s the batched PUT median rose to 582 ms and p95 to 1.16 s; PUTs still
accounted for 95.3% of object-call time. This is a client-observed **object-write
path bottleneck**, not proof of an absolute physical-HDD ceiling.

The current archive at 50/s made 29,579 PUT attempts for 24,000 final objects,
35,579 GETs and 96,309 HEADs. Its 5,579 extra PUTs are journal contention rather
than newly retained data. Median GET/HEAD latency also increased to 23/15 ms
under this workload. It used only about 0.69 CPU cores on average. The client
CPU quota and bulk network bandwidth do not explain that result.

Live TrueNAS reads confirmed the S3 dataset uses the HDD pool, `sync=standard`,
128 KiB records, LZ4 and `atime=off`. NAS CPU was low during sampled intervals.
The reporting API's disk-busy aggregate can exceed 100, so it is not presented
as one disk's utilization. These observations do not isolate physical disks,
ZFS, gateway internals and other appliance activity. No storage tuning, cache
dropping, filesystem changes or power-loss test was performed.

## Method

`metadata_load.py` offers distinct real HTML bodies at a fixed rate, independently
of completion. Every destination starts empty. Reusing only capture IDs, URLs or
already-uploaded bodies does not count as new ingestion. Each trial asserts one
new payload creation per input and independently verifies every committed body
after the load. These are replayed real captures, not new live browser visits.

Source fixture download and initial integrity checks happen before timing. Timed
work includes local fixture reading/decompression, production HTML identification,
fresh Zstd compression, actual conditional S3 upload, full read-back verification,
metadata publication and publication read-back. Both variants preserve identical
capture facts and payload bytes. Source objects are only read; all destinations
are validated UUID prefixes under `repository/benchmarks/archive-layout-*`.

The current variant uses production `Archive.commit`: individual capture envelope,
journal event and receipt. The experimental variant publishes complete capture
envelopes together in one Zstd object in a contiguous, sixteen-shard journal. A
conditional create is the commit point. It flushes at 64 captures or one second
after the first upload-ready capture enters the buffer. The time bound concerns
buffering, not total publication latency: uploads, verification and queued work
still count. One metadata batch cannot exceed 8 MiB expanded.

The primary comparison has a 64-lane storage-call budget. Current lanes upload and publish
one capture; batching divides it into 48 body lanes and 16 metadata lanes. The
client HTTP pool is 96 connections, avoiding an artificially undersized pool.
The diagnostic pod is capped at four CPU cores and 6 GiB RAM. Input queues retain
the finite test workload; they are load-generator backlogs, not a proposed
unbounded production buffer. Each offered capture's latency starts at its
scheduled arrival, so waiting behind a saturated worker is included.

Two additional controls isolate software choices. `current_shared` retains the
production format and commit protocol but shares one journal-head cache and
uses one in-process append lock per shard. It removes avoidable competition
between this process's writers; conditional creation still arbitrates other
processes. A second batched run raises the total lane budget to 128 (112 body
lanes and 16 metadata lanes), with an HTTP pool of 144 connections. Neither
control changes the hardware or bypasses payload verification.

Measurements include one-second backlog samples, final drain time, p50/p95/p99
publication latency, logical object operations, actual botocore HTTP attempts
(including unsuccessful requests/retries), operation duration, process CPU time,
object count/bytes, complete metadata replay and complete payload verification.
GET durations include reading the entire compressed response. PUT durations cover
the synchronous object-store call; they do not identify the server's internal
filesystem or disk wait. Filesystem allocation, xattrs, snapshots and replication
are outside logical object byte counts. NAS caches are not dropped on a shared lab.

## Correctness and remaining integration work

`metadata_correctness.py` kills a separate process after durable writes and
replays the frozen work from a fresh process cache. It covers body, envelope,
journal and receipt boundaries for the current layout, and body and batch
publication for the experimental layout. Additional cases inject a lost reply
after publication, overlap four deliveries, retry with changed batch boundaries,
verify shared bodies and reject corrupt metadata.

All cases passed both locally and against the real S3 gateway, including the
shared-head coordination control. The S3 run asserted empty destinations after
each case and ended with zero remaining objects. These are process termination
and injected lost-reply tests, not appliance power-loss tests or a loaded
NATS/outbox recovery drill.

Duplicate journal references are permitted; deterministic capture identities must
collapse them during materialization. The replay verifier uses a dictionary only
to check the bounded fixture; it is not a proposed corpus-wide index.

This is not a drop-in production implementation. In particular, the batch format
detects conflicting capture facts during replay, rather than doing the current
per-capture conflict check before acknowledging a retry. There is no per-capture
metadata lookup index, retirement API, software manifest integration or running
NATS/outbox adapter for the new format. Those features must preserve the measured
operation reduction, or their cost must be benchmarked before adoption. Durable
upstream ownership must retain unacknowledged work when a buffer/process dies.

The archive load excludes Postgres/outbox and NATS scheduling. It measures the
storage publication path, not browser acquisition or complete product throughput.
The tests do not establish behavior after years of retained objects, full-disk
conditions, a cold-cache corpus scan or concurrent production rebuilds. Repeated
reads can benefit from the NAS cache. Metadata replay still scales with retained
capture count; batching reduces the number of requests rather than making a
complete rebuild constant-time. A production adoption needs a longer soak with
bounded durable delivery and the final metadata lookup/retirement contracts.

## Materialization measurement

`material_capacity.py` runs the actual materializer, production raw reader,
Postgres write claims, parser, ClickHouse writes and post-insert verification in
isolated databases. It uses batches of up to 32 captures and 32 MiB of declared
HTML. One-, four- and eight-process trials use identical input and compare final sorted
output digests and failures. All successful captures must exist exactly once.

For diagnosis only, a failed batch is retried one capture at a time to account for
every input. The production worker instead blocks publication on an unresolved
failed batch. Reporting successful rows/second is therefore not a claim that a
build containing those failed inputs is publishable. NATS, planner checkpoints
and publication controls are not included in this capacity measurement.

The replacement homelab VMs expose AVX/AVX2, and the pinned ClickHouse 26.8.2.7
server starts and answers queries there. The earlier CPU-instruction blocker is
resolved. The disposable materialization worker has an eight-vCPU quota;
ClickHouse has three vCPUs and 4 GiB RAM, separately from the worker. These are
the diagnostic allocations, not the full VMs' capacities.

A local two-capture reproduction exposed a software correctness issue before
the lab capacity runs: row admission measures compact JSON, while the HTTP
client serializes JSON with spaces. An admitted row can therefore exceed the
request's 32 MiB limit before it is sent. The resulting exception retains write
claims, causing subsequent retries to report a claim conflict instead of the
original size problem. This is not ClickHouse CPU saturation or a disk failure.

The explicit `--compact-json` control changes only the benchmark child process's
wire serializer to match admission. The same two inputs then materialized
successfully (2/2 versus 0/2 capture rows). Production code is unchanged. A fix
should align admission with actual wire bytes and distinguish known pre-send
failures from uncertain remote writes when retaining claims.

| Wire encoding | Workers | Seconds | Successful captures/s | Capture rows | Failures |
| --- | ---: | ---: | ---: | ---: | ---: |
| Current | 1 | 184.40 | 10.84 | 1,998 | 2 |
| Current | 4 | 50.41 | 39.63 | 1,998 | 2 |
| Current | 8 | 38.77 | 51.53 | 1,998 | 2 |
| Compact control | 1 | 159.25 | 12.56 | 2,000 | 0 |
| Compact control | 4 | 45.83 | 43.64 | 2,000 | 0 |
| Compact control | 8 | 31.35 | 63.80 | 2,000 | 0 |

The unmodified runs all reproduced the same two failures and identical partial
output (1,998 captures and 1,999 documents). None is a publishable complete build.
All compact-control runs produced exactly 2,000 captures and 2,000 documents,
with identical sorted identity/output-digest fingerprints across worker counts.
Each clean run read all 100,657,594 compressed source bytes through the actual
S3 raw reader, with 2,000 GETs, 6,000 HEADs and zero source writes.

The eight-process clean run used 147.50 worker CPU seconds over 31.35 wall
seconds, or 4.71 CPU cores on average; database CPU is additional. The worker's
peak cgroup memory across the trials was about 3.22 GiB, below its 8 GiB limit,
with no OOM events. These finite trials show useful parallelism and capacity
above 50/s for this sample. They do not establish 100/s materialization capacity,
maximum homelab throughput, or combined ingestion/rebuild performance.

## Exploratory failures

The initial 9,000-capture baseline completed 8,994 publications in 539.9 seconds:
five inputs were rejected because their redundant S3 metadata URL was non-ASCII;
one was rejected with `MetadataTooLarge`. This run is diagnostic evidence, not a
successful acceptance run. Its exact destination was cleaned after confirming
those errors.

For final comparisons, both variants percent-encode the redundant S3 URL header
and replace headers longer than 512 characters with a SHA-256 URN. The complete
original URL remains unchanged in the capture envelope. No inputs are dropped,
and HTML bytes are unchanged. This adaptation is benchmark-only. The existing
production `RawHtmlRepository.put` header behavior needs a separate fix; retries
alone do not cure either error.

## Production work exposed by the experiment

1. Fix redundant S3 URL metadata handling, without changing the complete capture
   facts in the archive. Remove or safely encode/bound that redundant header;
   retries cannot repair an invalid header.
2. Match ClickHouse admission to actual serialized bytes. Report the original
   failure and release claims for provably unsent work, while preserving claims
   for uncertain remote writes. The compact control proves the sizing issue is
   repairable without discarding the troublesome documents.
3. Implement a bounded durable metadata-batching worker with the final lookup,
   conflict and retirement contracts. Preserve frozen unacknowledged work across
   restarts and retain the operation reduction demonstrated here.
4. Run a longer combined production-worker soak at 50/s, including process
   failures and growing retained state, before converting the retained corpus.
   The owner considers 50/s sufficient; further 100/s testing is optional
   headroom work, not a conversion gate. The benchmark prototype is not deployed
   product code.

## Layout recommendation and remaining sign-off

For the measured ingestion workload, separate content-addressed Zstd bodies and
bounded immutable Zstd JSON metadata batches are the smaller working direction.
The new [rebuild gate](REBUILD_CAPACITY.md) holds permanent body-file layout
sign-off open; keep Zstd and content addressing while comparing indexed packs.
Preserve distinct observation metadata when bodies repeat.
The metadata batch itself is the journal record and publication point; do not
restore an envelope, event and receipt object for every capture. Keep small
manifests, archived software and permanent retirement tombstones.

The existing body key already has two levels of hash fanout:
`html/sha256/<first-two-hex>/<next-two-hex>/<sha256>.html.zst`.
For journal batches, use a deterministic sequence-range directory, for example
`raw/corpus/v1/journal/<shard>/<range>/<sequence>.json.zst`, with
`range = (sequence - 1) // 4096`. This caps batch files per leaf directory at
4,096 without introducing a directory index. The experimental flat sixteen
directories would otherwise average about 976,563 files each for a billion
captures in full 64-capture batches; time/byte flushing can produce more files.
This range directory is a proposed production refinement, not a measured
variant. VersityGW [maps object-key slashes to actual directories](https://github.com/versity/versitygw/wiki/POSIX-Backend#object-name-mapping),
so the distinction matters on this backend.

An internal capture locator must include its metadata object key and record
position, validated by capture identity/digest. ClickHouse can store this
reconstructible locator; outstanding operational work can carry it too. A normal
lookup then reads one bounded metadata object. Archive-only recovery streams
the journals and rebuilds locators; it must not need a pre-existing ClickHouse
index or a permanent Postgres corpus map. Bare-ID metadata lookup during a
complete database loss must not silently fall back to scanning the corpus on a
request path.

Before calling the final protocol ready, settle capture-conflict enforcement
(the prototype detects it only on replay), retry publication after a lost ACK,
manifest cuts, and deletion replay. These must not reintroduce per-capture
publication objects. Physical deletion of shared bodies remains a separate
retained-reference reclamation operation; tombstones alone do not reclaim bytes.

Then run the final publisher and actual workers together in isolation for a
proposed 30-minute, 50-new-body/s trial with an active historical backfill.
Require no permanently growing live backlog, continuing historical progress,
proposed archive-publication p95 below 10 seconds, bounded resources, and full
post-run identity/payload verification. Include kill/restart, lost notification,
duplicate delivery and deletion cases, plus a separate fresh-database restore
from the raw archive. Reuse the existing recovery harness rather than adding
another recovery subsystem. Exercise range-directory rollover and a populated
namespace; a two-minute empty-prefix test does not establish aged-store capacity.

The 63.80/s material result measures one build in isolation and excludes journal
discovery. At that fixed rate, one million unique documents take roughly 4.35
hours, 100 million take 18 days, and a billion take 181 days. These are arithmetic
projections, not measured large-corpus timings. An old serving recipe and a new
candidate both need live materialization during a rebuild, in addition to
historical work. Therefore 50/s archive ingestion alone does not prove acceptable
full-rebuild time. The final combined trial must budget those workloads together.

Steady-state publication must avoid corpus-wide listing, global in-memory
deduplication sets and rewrites of old segments. Full rebuilds, backups and
physical garbage collection still scale with retained data. One body object per
unique content remains a real filesystem/object-count cost; this experiment
does not certify a billion-file NAS. Content identities and versioned archive
contracts should survive a deliberate future physical-layout migration if
large-corpus measurements eventually justify body packing.

## Verification and cleanup

`make check` passed: 458 application tests discovered (41 skipped), 23 Python SDK
tests discovered (6 skipped), and the repository's frontend checks/builds.
The benchmark files compile and the
targeted Ruff error checks pass. The local and real-S3 crash/retry suites pass.

All six S3 run roots, including the exploratory failed run, were independently
checked empty with a final prefix query. All temporary material databases were
dropped; the local application's existing material/public databases were left
alone. Diagnostic Kubernetes resources were removed after retrieving results.
The retained corpus was read only, and no production application or homelab
deployment configuration was changed.

## Reproduction

Run using the locked Periplus Python environment. The existing temporary
`lab-pod.yaml`, `material-pod.yaml` and `lab-databases.yaml` create isolated diagnostics, not managed
production resources. Copy the source and benchmark scripts into `/work`, along
with the frozen inventory and the existing pinned warcio/six fixture dependencies.

```sh
PYTHONPATH=/work python /work/archive/metadata_load.py \
  --inventory /work/inventory.jsonl.gz --cache /work/cache \
  --output /work/final50.jsonl --rate 50 --seconds 120 --kinds batch current
PYTHONPATH=/work python /work/archive/metadata_load.py \
  --inventory /work/inventory.jsonl.gz --cache /work/cache \
  --output /work/final100.jsonl --rate 100 --seconds 120 --kinds batch current
PYTHONPATH=/work python /work/archive/metadata_load.py \
  --inventory /work/inventory.jsonl.gz --cache /work/cache \
  --output /work/shared50.jsonl --rate 50 --seconds 120 --kinds current_shared
PYTHONPATH=/work python /work/archive/metadata_load.py \
  --inventory /work/inventory.jsonl.gz --cache /work/cache \
  --output /work/batch100_w128.jsonl --rate 100 --seconds 120 --workers 128 --kinds batch
PYTHONPATH=/work python /work/archive/metadata_correctness.py \
  --s3 --output /work/correctness.jsonl
PYTHONPATH=/work/project/packages/periplus/src:/work python /work/archive/material_capacity.py \
  --lab --inventory /work/inventory.jsonl.gz --cache /work/cache \
  --output /work/material-lab.jsonl --size 2000 --workers 1 4 8
PYTHONPATH=/work/project/packages/periplus/src:/work python /work/archive/material_capacity.py \
  --lab --inventory /work/inventory.jsonl.gz --cache /work/cache \
  --output /work/material-lab-compact.jsonl --size 2000 --workers 1 4 8 --compact-json
```

Run storage and material capacity trials separately. Retrieve results before
deleting the diagnostic resources. Materialization needs the source copied to
`/work/project/packages/periplus/src/periplus`, preserving the package's expected
project-root depth. On a failed run, inspect its logged UUID and
clean only that exact destination; never delete the source repository prefix.
Generated captures, source URLs, fixtures, credentials and logs are not committed.
