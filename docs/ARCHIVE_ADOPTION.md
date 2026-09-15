# Batched archive implementation and homelab adoption — 2026-09-15

## Chosen launch contract

Separate immutable, content-addressed Zstandard HTML bodies, with capture facts
embedded in immutable metadata batches. Body packing is not a launch prerequisite;
a later body migration remains possible. This decision does not establish
billion-document weekly rebuild capacity. See [storage](STORAGE.md) and the
[earlier capacity measurements](../benchmarks/archive/REBUILD_CAPACITY.md).

The production writer groups up to 64 captures, divides them across 16 UUID
shards, and conditionally creates each shard's next metadata segment. A segment
contains complete capture facts and a digest. Logical event positions remain
contiguous inside variable-sized segments. A reader can start or checkpoint
inside a segment. New journal directories start every 4,096 segments.

There is no individual capture-envelope or commit-receipt object. ClickHouse
stores an exact `batch-key#offset` locator; NATS carries a small committed event
reference. Neither database is needed to explain an archived capture. Permanent
retirement tombstones remain separate so a recovery using an older manifest
still respects subsequent retirement. Metadata is retained when an unreferenced
body is reclaimed.

A disposable SQLite lookup, rebuilt from the journal, detects conflicting facts
for the same capture identity. Conditional segment creation arbitrates competing
writers. An uncertain write is retried at its original committed position, rather
than appending duplicate capture events. The lookup uses an 8 MiB SQLite pager
cache and local temporary disk; batch and locator caches are separately bounded.
It is not an archive authority, extra service or backup dependency. Cold publisher
startup currently replays metadata: its duration and temporary disk footprint grow
with the corpus. Durable derived checkpoints are not implemented. Readers holding
explicit references and streaming rebuilds bypass this lookup.

The crawler outbox publishes bounded groups through this writer. Claims still
protect exact capture/body identities, and Postgres outbox rows settle only after
archive publication and NATS announcements. Partial publication, lost replies
and missing announcements remain retriable. Low traffic and random shard
assignment produce partially filled batches; 64 records per object is a maximum,
not an assumed occupancy.

Raw HTML writes no longer copy per-visit URL/timestamp fields into S3 headers.
Those facts are in capture metadata. This removes the non-ASCII URL header failure
without modifying retained bodies or truncating their archived URLs.

## Retained corpus handoff

**Adoption completed:** all 229,023 observations were read back with exactly the
frozen evidence fingerprint. The recovery manifest, matching source/dependency
bundle and adoption audit are published in the retained repository.

Recovery manifest (repository-relative key):

```text
raw/corpus/v1/manifests/cfc66343e3ddc4e36c944da962eedbf5354234f1a2c9954d294aeae7d793a65a.json.zst
```

Preserved software:

```text
raw/corpus/v1/software/006a322411bb93345198bc22b7f77bed234af501f18efceb946720aebb560b86.tar
```

Adoption audit:

```text
raw/corpus/v1/adoption/c4ecf4704456e02f608b4e4bc49f52920517107d9e8d7f07ea15b343a25c6504.json
```

The manifest contains 229,023 logical journal events and recipe
`41d7c163fe934978f518fb1872552a1e79966853cbd5a4d3a776e344539a3aab`.
The verification report SHA-256 recorded in the audit is
`b89060ecd577e15861c36686ef0cd4d6c9907b4bd1b1e4b9593553327c7b1e20`.

An independent process verified the manifest, software hash, audit hash and all
229,023 capture identities/digests, rejecting duplicates. The archive contains
**3,586 metadata segments / 32,099,984 bytes**, plus the manifest, software and
audit (3,589 objects / 33,340,302 bytes total). A fresh publisher rebuilt the
229,023-row lookup in **12.53 seconds**; SQLite's logical database size was
16,453,632 bytes. Exact lookup checks passed for 32 captures across all shards.
These are measurements of this retained corpus, not linear-scale predictions.
The downloaded software bundle also matches all 205 local source files and the
dependency locks; its manifest copy was checksum-verified locally.

The frozen DuckLake snapshot is **426789**: **229,023 observations**, of which
185,190 reference HTML and 43,833 have no HTML. There are **176,750 unique bodies**.
All unique bodies passed compressed-length, decoded-length and SHA-256 checks.
The frozen source already excludes the 528 retired observation IDs. The retained
bodies total 9,308,549,812 compressed bytes and 70,312,970,582 decoded bytes
(logical object sizes, not filesystem allocation).

The complete verification ran against the same S3 repository in the same isolated
pod used for adoption, while old writers remained stopped. Its initial report did
not include repository identity; an operator attestation bound that completed
report to the pod's unchanged startup configuration. The original report, complete
verification log, context attestation and context-bound report are retained
separately. The converter requires exact equality of the frozen input counts,
fingerprint, inventory hash and repository identity before reusing that proof.
It records the proof's SHA-256 in the final audit. This is one complete body
verification, not a claim that two independent body scans were performed.

The adopter refuses body writes and all deletion calls. It reads every committed
capture back and requires exact equality with the frozen inventory before
publishing the recovery manifest and software bundle. Existing unrelated evidence
or conflicting capture facts fail closed. The one-time converter is outside the
runtime: [operator instructions](../scripts/archive_adoption/README.md).

The retained capture fingerprint is
`b74d5bcb7d13f148b896aa7de9ccb2bd8da522a0e8e249f19a714552ee4bdec7`.
The inventory SHA-256 is
`9511347ca854305aa837f894881213e0dd0a2292fd0e0342beb23a63a19a18e2`.

## Production protocol validation

A final isolated S3 trial offered **6,000 genuinely new bodies/captures at 50/s
for 120 seconds**, using `metadata_load.py --kinds production --workers 128
--flush 1 --seconds 120 --rate 50`. It used the actual archive implementation,
actual uploads and body verification. The fixture includes the non-ASCII URLs
that previously failed as S3 header values.

| Measurement | Production batching |
| --- | ---: |
| Published and verified captures | 6,000 |
| Failed captures | 0 |
| Completion rate, including final drain | 48.01/s |
| Backlog at end of arrivals | 346 |
| Final drain | 5.00 s |
| Publication latency p50 / p95 / p99 | 6.74 / 9.78 / 10.63 s |
| Maximum publication latency | 12.38 s |
| Load HTTP attempts: PUT / GET / HEAD | 7,660 / 7,660 / 30,608 |
| Stored objects: bodies / metadata | 6,000 / 1,660 |
| Total logical stored bytes | 313,328,485 |
| Fresh-reader metadata verification | 18.84 s |

The trial ended successfully with exact evidence fingerprint equality, body
verification and complete cleanup of its UUID destination. Its initial backlog
grew, then decreased near the end; it is a short offered-load trial, not proof of
an all-day steady state. It excludes CDP acquisition, Postgres outbox settlement,
NATS delivery and materialization. The earlier 100/s results used the prototype;
production 100/s headroom has not been established by this final test.

`production_correctness.py` passed locally and on real S3: process death after a
body write and after metadata publication, overlapping publishers, exact retries,
conflicting facts, interruption after a retirement tombstone, retirement replay
and cleanup. No retained objects were used as test destinations.

The isolated Docker recovery drill used fresh Postgres, ClickHouse and NATS and a
read-only copied archive. It recovered 255 expected captures after killing an
active worker and waiting its real ownership lease; evidence digests matched,
no duplicates appeared, business tables stayed empty, and retirement was honored.
A second fresh-stack smoke test using the final source/image recovered 256
captures (including the live fixture added by the first drill), matching every
capture identity/digest in 13.47 seconds. The second test did not inject a kill;
the first kill test preceded a change to SQLite temporary-file lifecycle.
That longer drill also passed live catch-up while history was paused, blocked
publication on a missing/corrupt payload, ordinary repair/retry using the real
claim expiry, cancellation, atomic publication and physical target reclamation.
Only the final cleanup clock was accelerated, after stopping all workers.

Final image:
`sha256:9406d98d0c9c5fbca65ee8a0ee7e0be8128b56d02080c5266e900e6e109dfb13`.
Final source recipe:
`41d7c163fe934978f518fb1872552a1e79966853cbd5a4d3a776e344539a3aab`.
`make check` passed: 464 application tests (41 skipped), 23 SDK tests (6 skipped),
and frontend checks/builds. Five converter-specific tests also passed.

Private logs, source snapshots, manifests and verification reports belong under
ignored `.artifacts/archive-adoption/`; inventories, URLs and database dumps must
not be committed. The manifest's software bundle preserves source and dependency
locks, but matching runtime/container dependencies must also remain available.

## Remaining deployment work

Temporary benchmark destinations, both Docker recovery stacks, and the adoption
pod/network policy have been removed. The old homelab deployment remains stopped,
with the old lake, raw bodies and
Postgres dumps preserved. Durable ClickHouse installation, a clean operational
database, the new Periplus release, a full retained-corpus materialization and
public-service cutover are the next separate operation. None is implied by raw
adoption or by the small Docker recovery proof. Delete old lake storage only
after that deployment's recovery and product acceptance checks pass.

Materialization still needs separate capacity work. The inventory contains **three unique bodies above the current 32 MiB HTML
input limit**; the largest is 83,815,376 decoded bytes. These bodies are verified
and retained. The worker's input limit and output-row admission must be resolved
before a complete retained-corpus rebuild can pass. This adoption does not drop
large captures to make recovery appear complete.
