# Rebuilds and archive-only recovery

A build is a set of derived ClickHouse tables plus sixteen pairs of checkpoints.
Creating one does not rewrite or publish the whole archive. The initial manifest
pins a small journal cut; historical ranges cover that cut while separate live
ranges cover later arrivals. Pausing a build pauses historical scheduling only.

## Execution and publication

The API creates a candidate with a recipe digest, target names and page size
(1–128 captures). A matching worker preserves its source/lock bundle, creates the
manifest and installs hidden tables/views. One planner per recipe publishes batch
UUIDs; all ingestor replicas running that recipe execute the shared queue.

Each replica has one material/import execution slot and a separate planner loop.
An available material batch takes priority over an import step. An import already
in progress finishes its bounded step before polling material work again. Live
and historical ranges continue sharing the existing recipe queue; this change
does not add strict priority between those two kinds of material batches. Under
sustained material backlog, Common Crawl imports intentionally wait. Native
capture admission remains governed by frontier controls and does not wait for
materialization; pause or reduce crawling if raw-to-material lag is undesirable.

There is at most one historical and one live batch per shard per build: 32
outstanding records, independent of total corpus size. Workers record batch ID,
worker ID, attempts and errors. Successful records collapse into contiguous
range cursors. Retry requeues failed records; cancellation changes the revision
so late completion cannot advance progress or publish the cancelled target.

A candidate becomes ready only when every initial range and the observed live
cut are covered without failed batches. Activation requires verification within
15 seconds and changes one Postgres publication pointer transactionally. A query
pins that pointer once; it cannot mix old HTML structure with new capture tables.
Bindings expire after 30 seconds and ClickHouse queries are bounded to 45 seconds.
A new rebuild waits while an older target is draining, bounding storage to the
serving, previous and candidate targets. The previous build stays available and receives live updates while its matching
workers remain deployed. On the next activation, the older previous target drains
and is physically dropped. Cancelled candidates also drain and are dropped.

Workers fail-stop after 300 seconds of bounded I/O. Ownership/draining lasts 610
seconds to cover an uncertain writer and remote work. A killed worker can therefore
cause a roughly ten-minute retry delay; shortening this without a server-side
fence would sacrifice correctness. Process suspension beyond that deadline is
outside this ownership contract.

## Changing parser software

The recipe hashes projection source, SQL and parser dependency versions. Old and
new workers use disjoint material subjects/consumers and planner leases. Keep the
old image's workers running while the new image backfills its candidate, so the
serving old interpretation continues receiving live captures. After publication
and drain, remove obsolete workers. Simply replacing every old worker cannot
continue executing its old recipe. Never disable the recipe check to bypass this.

## Recovery procedure

1. Preserve a manifest with `periplus-archive manifest`. Back up the complete raw
   metadata-batch journal, retained payloads, all tombstones and named software bundle.
   A manifest names a cut; copying only the manifest is not a backup.
2. Start fresh Postgres, ClickHouse and NATS. Configure the archive read-only and
   use the software recipe recorded in the manifest.
3. Run `periplus-setup --restore-manifest <repository-key>` once against the empty
   control database, then start shared ingestor workers. An exact retry with the same
   bootstrap manifest is safe; conflicting restore intent is rejected.
4. Wait until the serving build has a verification timestamp and zero source lag
   and failed batches. Initial bootstrap queries may otherwise see partial recovery.
5. Verify counts, stable identities and representative public queries. Business
   tables remain empty. Restore Postgres separately to recover customer operations.

`periplus-archive verify --manifest <key>` verifies archived events and retained
payload integrity without a database or queue connection. Verification can take
as long as reading the retained bytes. Tombstones are checked even when replaying
an older manifest, so a deleted capture cannot be resurrected.

Workers stream logical event ranges from metadata segments and read complete
capture facts by segment/offset. They do not build the publisher's UUID lookup
index. A checkpoint can fall inside a segment; retries verify the same physical
record and preserve the existing contiguous logical cursor semantics.

The software bundle includes source and the dependency lock; the runtime still
needs the matching Python/dependency artifacts or preserved container image.
Keep image artifacts too for offline disaster recovery. This is reproducible
input preservation, not an automatic old-software deployment service.

## Reproducible proof

`scripts/archive_recovery_smoke.py` copies a bounded local archive and creates a
fresh Docker stack on an internal network with no original service access. The
copy is mounted read-only. It checks empty business tables, parallel workers,
kill/restart recovery, exact capture digests, duplicates and tombstones. Evidence
is written beneath `.artifacts/archive-recovery/`; generated files are not committed.

The canonical proof result and remaining scale limits belong in
[VALIDATION.md](VALIDATION.md). Unit tests additionally cover journal races, lost
responses, permanent failures, retry, pause, cancellation and publication fences.
