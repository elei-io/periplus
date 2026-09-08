# Storage dashboard

The admin page `/data/storage` reads `GET /operations/storage` through the admin gateway.
Public credentials cannot access this endpoint. It creates no stored measurements, counters,
telemetry, retention history, or new persistence paths. A process-local cache reuses one report
for 60 seconds and concurrent requests share one in-flight collection.

## Measurement semantics

The accounted footprint is a partial sum of independently collected sources:

- Raw documents: object metadata under the existing repository's `html/` and `documents/`
  prefixes, including objects no longer referenced by retained documents.
- Current lake files: data and delete file sizes from `ducklake_list_files` for current tables.
  Inlined rows consume metadata database storage, not additional registered-file bytes.
- Control Postgres: `pg_database_size`, with the largest 200 user relations broken down into
  table/TOAST and index bytes. Relation breakdowns are not added to the database total again.
- DuckLake metadata: metadata Postgres database size or local metadata database file size.
- Transient objects: existing `runtime/` and `material/staging/` repository prefixes.
- JetStream: bytes reported by the existing crawl, catalogue-work and dead-letter streams.
  Reads never reconcile streams or provision consumers.

Historical/unreferenced lake files, other object prefixes, WAL, backups, NATS KV and physical
replication overhead are outside coverage. No total physical capacity or runway is inferred.
Object inventories are non-transactional and stop after 10,000 objects or a five-second scan
budget checked between metadata reads. Partial inventories retain a labelled lower bound.
Underlying store I/O remains subject to the existing client's timeouts. Failed sources return
null, never zero. Source paths, connection strings, keys and native errors are not exposed.

Evidence counts, retained unique bytes, row estimates, file sizes and retirement state are read
in one read-only DuckLake transaction with a 15-second interrupt deadline after attachment.
At most 200 tables are measured; exceeding that bound marks table coverage partial. The API
waits up to 25 seconds for a collection and retains timed-out work for later readers instead of
starting overlapping work. Shutdown drains any existing collection. Control/metadata Postgres
queries use read-only transactions and five-second statement timeouts.

Document referenced bytes sum document records. Unique retained bytes deduplicate by object
key. Both per-observation ratios use all retained observations as their denominator and are
unknown when there are no observations. Neither ratio includes projections or operational data.

## Reclamation and unavailable measurements

The page reads existing object-retirement receipts, grouped into pending snapshot boundary,
awaiting snapshot clearance, and reader-grace/awaiting-reclaimer stages. Expected bytes in this
queue do not prove reclaimable capacity. Cleared snapshots alone do not establish the janitor's
current grace setting, publication claims or later references. Retirement identity counts are
compact replay receipts, not a deletion-throughput history.

No historical size samples are available, so growth, additions-versus-reclamation charts, and
reclaimed-byte totals remain explicitly unavailable. The API does not infer the running
janitor's mode or health from its own environment. Janitor run history and LakeDucktor
maintenance history are likewise unavailable from these sources. No additional records are
written to fill those gaps.
