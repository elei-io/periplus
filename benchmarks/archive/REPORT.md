# Archive layout comparison — 15 September 2026

The useful direction is **Zstandard plus content addressing, with fewer archive
writes through batching/packing**. WARC remains useful as an interchange format;
the measured gzip WARC layout offers no native-storage advantage here. No
production runtime or original corpus has been changed.

The large improvement is ingestion. For 1,000 captures, separate objects needed
1,072 seconds and 3,997 objects; packed CAS needed about 16 seconds and 36 objects.
Their sizes were almost identical: 218.4 versus 219.1 MB.
These prototype results justify further packing work, not deploying the prototype.
The comparison writes fresh isolated destinations. An in-place adoption that
keeps today's existing body objects would avoid their copy PUTs; the current
per-capture metadata/journal/receipt writes would still remain.

### Homelab storage results

| Measurement, same 1,000 captures | Separate objects | Packed CAS | Packed WARC |
| --- | ---: | ---: | ---: |
| All object bytes, decimal MB | 218.40 | 219.12 | 298.50 |
| Objects, including metadata/indexes | 3,997 | 36 | 36 |
| Adoption/ingestion seconds | 1,071.87 | 15.63 | 29.48 |
| Verified sequential body scan, seconds | 4.45 | 3.49 | 4.80 |
| Random download p95, milliseconds | 4.28 | 5.06 | 6.90 |
| Enumerate all committed captures, seconds | 3.86 | 0.063 | 0.061 |
| Reconstruct deleted derived indexes, seconds | Not applicable | 4.46 | 5.80 |

Each packed archive contains 12 packs, 12 indexes and 12 commit objects. This
table uses one completed storage trial per format with the final retirement
contract; earlier packed trials confirmed similar ingestion times. It is enough
to identify the large write-cost difference, not rank small latency differences.
Incomplete repetitions are excluded. This is a practical decision benchmark,
not a statistical confidence interval or production capacity test.

The baseline made 3,997 PUTs, 5,000 GETs and 4,984 HEAD-like logical calls during
adoption, using only 20.1 CPU seconds. Packed CAS made 36 PUTs and used 5.6 CPU
seconds. The difference is consistent with the cost of many individual durable
writes. Reads of recently written objects were already fast. Storage caches were
not dropped on the shared lab, so these are **not cold-disk rebuild timings**.

All formats deduplicate exact bodies. The baseline already saved duplicate body
writes; WARC revisit records do not create a unique advantage there. Packing
nearly eliminates small objects, but CAS saved no bytes in this prototype because
its framing and indexes also take space.

### Retirement and reclamation

The same 100 scattered captures were retired in each archive. Every remaining
capture and body was verified again, and original retired capture facts remain
available as audit evidence. No shared body still needed by a retained capture
was deleted.

| Operation | Separate objects | Packed CAS | Packed WARC |
| --- | ---: | ---: | ---: |
| Record logical retirement, seconds | 82.78 | 0.22 | 0.24 |
| Physical reclamation, seconds | 0.35 | 8.49 | 16.55 |
| Bytes rewritten during reclamation, MB | 0 | 66.73 | 96.19 |
| Net object bytes reclaimed, MB | 8.71 | 8.89 | 19.83 |

Packed retirement is one batch tombstone containing the original capture facts;
the current archive writes per-capture retirement records and journal receipts.
This is another **batching difference**, not an inherent WARC/CAS property.
Reclamation scanned all 12 packs and rewrote seven affected packs. CAS rewrote
about 7.5 bytes per net byte reclaimed; WARC about 4.9. Different compressed
sizes and audit representations explain the different net bytes reclaimed.

Replacement packs are published before old ones are removed. Extra object space
is therefore needed for a replacement pack plus its index/commit at a time;
this is a bound from the algorithm, not a measured ZFS space peak. Concurrent
readers are outside this offline prototype. Filesystem snapshots can also retain
deleted bytes after the S3 objects have disappeared.

### Continuous arrivals and duplicates

For 20 ordinary captures flushed individually, average durable write time was
1.01 seconds per capture with separate objects and 0.76 seconds with either
packed layout. Object counts were 80 versus 60. **The bulk speedup does not apply
to individual arrivals.** A production batcher needs an explicit short flush
deadline; it must acknowledge only after the archive commit is durable.

Retiring this whole cohort needed no payload rewrites in any format. Body
reclamation took 0.05 seconds for separate objects and 0.18 seconds for the
packed layouts. Capture audit evidence remained after payload deletion.

A separate synthetic duplicate-heavy test reused 20 real bodies across 200
distinct captures:

| Measurement | Separate objects | Packed CAS | Packed WARC |
| --- | ---: | ---: | ---: |
| Stored bytes | 866,922 | 965,798 | 1,045,349 |
| Objects | 620 | 3 | 3 |
| Ingestion seconds | 152.68 | 0.81 | 0.79 |

All three stored each unique body once. Retiring 20 captures left every body
still referenced, and verification confirmed that no needed body disappeared.
The packed prototype nevertheless rewrote capture metadata during reclamation.
A production compactor should avoid a rewrite when it reclaims negligible space.
This workload demonstrates metadata batching and shared-body safety; its 90%
duplicate rate is synthetic, not a claim about the internet.

Four concurrent writers were also tested on the same 200 randomly selected
captures per format. Separate objects took 114.49 seconds; CAS packs took 2.36
seconds and WARC packs 2.46 seconds. All committed captures and retained bodies
verified after recovery and reclamation. Writers had fixed, disjoint body-hash
ownership inside one four-vCPU pod. This confirms the write-cost difference
under concurrency, but does not prove worker reassignment, overlapping retries
or production failover. It is a different sample from the 1,000-capture trial;
the two cannot establish a precise single-to-four-writer scaling factor.

### Compression on ordinary pages

An independent, deterministic random sample has 10,000 captures and 9,932 unique
bodies, totalling 3,669,483,817 uncompressed bytes. Both compressors reproduced the
exact input bytes:

| Body codec | Stored bytes | CPU seconds |
| --- | ---: | ---: |
| Zstandard level 6 | 489,680,237 | 13.00 |
| gzip level 9 | 649,555,551 | 61.55 |

Gzip needed **32.7% more space and 4.7 times the compression CPU** on this sample.
This isolates the body codec from packing and metadata. It compares the codecs
actually used by these candidates, not every compression format WARC could use.
Packing independently compressed bodies does not inherently save payload bytes.
Exact-body deduplication is also available to every candidate.

Random-sample fingerprint:
`40d7d71487c2d5a766ccc76bc52af0afec8ab1b38b7a31d95a710417e1c465f4`.

## Inputs and proof

The frozen homelab inventory has 185,190 captures, 176,750 unique bodies,
70,312,970,582 uncompressed body bytes and 9,308,549,812 stored body bytes.
These are inventory totals, not a full verification of every source object.

The storage stress sample contains 1,000 captures and 997 unique bodies:
888,663,805 uncompressed bytes and 217,610,765 existing compressed bytes. It
deliberately includes the largest bodies, up to 83,815,376 bytes. Its unusually
large average must not be projected to a billion pages.

Frozen inventory fingerprint:
`4079adbcaee9e723ee98fe6adc96d11de3aed010295ecac7ba988592d00c64d4`.
Stress sample fingerprint:
`e395fc1c36a87d7354184ef5dda0bf2d8237df9f0076c6e3dc30e4474a63602b`.

The local actual-materializer proof is complete:

| Archive layout | Materialization seconds | Capture rows | Document rows | Rejected captures |
| --- | ---: | ---: | ---: | ---: |
| Separate objects | 153.08 | 987 | 984 | 13 |
| Packed CAS | 154.36 | 987 | 984 | 13 |
| Packed WARC | 150.51 | 987 | 984 | 13 |

All three produced exactly the same sorted output digests and failures. This is
one local run per layout, using real Postgres claims and ClickHouse writes, one
capture at a time. It is not a homelab throughput claim. The small timing spread
does not establish a parser-performance winner.

Capture output fingerprint:
`0080eedaf1268bb1fbc5e9d03512f6fc4d89330f7779aa7a677c45b15073025f`.
Document output fingerprint:
`68aacaa3100c43440dc33de619f6dbe871a2492109361bdd024d14a622803093`.

Three captures exceed the 32 MiB HTML input limit. Ten more exceed the 31 MiB
material row output limit. The archive preserves all 1,000 captures, but these
13 are not currently queryable material. Because the sample includes extremes,
13/1,000 is not an estimate of the corpus failure rate. Rebuild admission and
failure reporting need to make this distinction visible.

## Homelab deployment blocker

The exact pinned amd64 ClickHouse image, `26.8.2.7`, exited with `Illegal
instruction` before server startup. The VM has SSE4.2 but no AVX/AVX2. Homelab's
declarative VM configuration currently uses `x86-64-v2-AES` in
`vms/__main__.py`; the physical Ryzen CPUs' capabilities are not passed through.
ClickHouse documents an x86-64-v3 requirement for this image family in its
[official image instructions](https://github.com/ClickHouse/ClickHouse/blob/master/docker/server/README.md).

Before a homelab rollout, change the VM CPU model through the homelab's managed
infrastructure workflow, arrange the required VM restart, verify the exposed
flags, and rerun the pinned image and rebuild proof. This experiment did not
restart a VM or change its CPU model. The temporary failed database deployment
was removed. Local materializer proof must not be substituted for this missing
homelab proof.

## Integration work that a packed archive would require

Packing is not a transparent file move with the current schema. `Capture.Payload`
includes `object_key`, `stored_bytes` and `storage_encoding`; those physical
details participate in the capture digest and are copied into material rows.
The experiment preserves them and adapts reads by content identity. Production
would need an explicit physical locator contract, updated download/rebuild
readers, and an archive identity decision before changing layouts.

The packed prototypes also trade away production guarantees: their index is
in memory, each body-hash partition has one writer, and reclamation is offline. A billion-row
locator map cannot simply be held in a Python dictionary. Rebuilds need bounded
streaming/index construction, and reclamation needs reader protection before
old packs are removed. Their quick writes do not prove those problems solved.

WARC's resource/metadata/revisit records pass the standard warcio reader and
digest checks. The comparison preserves rendered HTML as resources; it cannot
recover original response headers or wire bytes that were never retained.
Customer WARC exports and SQL-selected training datasets remain separate product
choices from the native physical archive.

The existing Common Crawl importer accepts a deliberately narrower subset:
complete HTTP 200 UTF-8 HTML response records without transfer/content encoding.
It explicitly rejects resource/revisit records. The experimental WARC packs
therefore need a dedicated archive reader; being WARC does not make them a
drop-in input to that importer. For the supported import subset, the code checks
that retained body bytes match the original HTTP block and preserves its headers.

## Recommended next implementation and exit criteria

1. **Keep Zstandard and content addressing.** There is no measured storage-saving
   reason to convert the retained corpus to gzip WARC. Choose customer export
   formats according to their use, independently of native storage.
2. **Target the archive's small metadata writes first.** Three of the current
   four objects for a new unique capture are capture/journal/receipt metadata.
   A metadata-only batching variant, retaining today's simple body lookup and
   deletion, is the next smaller experiment. Its gains have not been measured
   here; do not assume it delivers the full-body packing speedup.
3. **Before full packing, settle the permanent contract.** Stable evidence
   identities, bounded on-disk locator/index construction, short durable flushes,
   overlapping-writer idempotency, and reader-safe reclamation need actual runtime
   implementations. Prefer reclaiming sufficiently dead packs or whole expired
   cohorts over repeatedly copying mostly live data.
4. **Fix the VM CPU exposure, then run the real homelab rebuild.** Start the pinned
   ClickHouse image, rebuild into an isolated target, reconcile every retained
   capture as materialized or explicitly rejected, and test resume/retry plus
   publication while live ingestion continues. The local proof here is not that
   service-level acceptance test.

The storage comparison is complete. The ClickHouse VM blocker and the runtime
guarantees above are concrete remaining work before production adoption.
Retained raw data has not been migrated or deleted.

## Reproduction and limitations

See [README.md](README.md) for the runnable scripts, frozen selection rules,
measurement boundaries and cleanup procedure. Raw results and source fixtures
remain under ignored `.artifacts/archive-layout/`; source URLs and HTML are not
committed. Experimental code lives only under `benchmarks/archive/`.

The lab benchmark runs on k3s-02 with a four-vCPU quota and 6 GiB memory limit,
against the existing private S3 endpoint and NAS. Python 3.14.7, Zstandard 0.25.0,
boto3 1.43.45, selectolax 0.4.11, Pydantic 2.13.5 and warcio 1.8.1 are used.
These are single-process results on a shared, otherwise lightly loaded lab.
The four-writer check uses four threads within the same resource limit.

Runtime source commit: `7afc1c3d26aa85a2eec5d360cd4fca9033afee6f`.
Homelab configuration commit: `03e5bedf24f3d7904db920fc017fea4976cc1c81`.

Validation passed: five benchmark test methods with format/failure subcases,
standard WARC digest verification, the real local materializer comparison,
all reported homelab round trips, and the repository's complete `make check`
(backend, SDK, shared packages and both frontends). The initial frontend check
found stale generated Next.js route types from the previous checkout; moving
that generated cache aside and rebuilding resolved it without a source change.

Cleanup was verified: all six run prefixes contain zero objects, both temporary
pods and their service/network policies were removed, and the temporary local
material databases were dropped. Local Compose services remain healthy. Raw
results and the stress-sample fixture cache remain in ignored local artifacts;
the larger codec sample can be reselected from the frozen inventory.

Object bytes include indexes and capture metadata, but not ZFS allocation,
filesystem metadata or retained snapshots. Common software bundles cancel out
between layouts. No full-corpus rebuild, sustained multiwriter load, concurrent
reader/reclamation race proof or billion-capture performance claim is made.
