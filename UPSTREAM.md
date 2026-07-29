# Upstream DuckLake and Quack feedback

Atlas uses the official DuckDB Python library, session-affine Quack attachments minted by
DuckBasin, and Basin-published JetStream CDC.

## Quack replica startup mutates DuckLake history

- **Atlas caller:** horizontally routed ingestion and materialization clients.
- **Evidence:** read-only scale-from-zero runs advanced DuckLake snapshots by replacing
  `duckbasin_access_policy` during replica startup.
- **Needed upstream contract:** replica startup must not persist per-replica catalogue DDL.
- **Atlas status:** Atlas ignores policy-view-only snapshots as application DDL.

## Quack remote scans drop non-default schema qualification

- **Atlas caller:** ingestion and materialization SQL against `ingest.*` and `material.*`.
- **Evidence:** local Quack-generated scans can drop schema qualification, while explicit remote SQL
  preserves it.
- **Needed upstream contract:** preserve catalogue and schema qualification, including identically
  named tables in different schemas.
- **Atlas status:** Atlas uses explicit trusted remote reads and schema-qualified Arrow transfers.

## Long Quack mutations can replay before query-completion acknowledgement

- **Atlas caller:** whole-table materialization refreshes in session-affine remote transactions.
- **Evidence:** long mutations can be replayed after reconnect before the original completion is
  acknowledged.
- **Needed upstream contract:** one long transactional statement executes exactly once and retains
  the same server connection through `COMMIT`.
- **Atlas status:** materialization CDC is replayable and idempotent, but exactly-once remote query
  acknowledgement still belongs upstream.

## Quack cannot MERGE a registered Arrow source into a remote DuckLake target

- **Atlas caller:** incremental fixed materializations uploading bounded Arrow projections.
- **Evidence:** DuckLake accepts `MERGE INTO` with a remote SQL source, while a registered Arrow
  `USING` relation fails through Quack with `Binder Error: Can only merge into base tables`.
- **Needed upstream contract:** forward `MERGE INTO remote_target USING registered_arrow_relation`
  through the session-affine Quack connection, preserving the surrounding remote transaction.
- **Atlas status:** Atlas holds the target operation lease, checks content-hash coverage, appends
  only absent immutable projections, and uses remote `MERGE` for scoped deletion sets.

## Quack Arrow inserts fragment one logical DuckLake write by streamed chunk

- **Atlas caller:** typed, bucket-grouped Arrow materialization rebuilds inserted through an
  attached Quack catalogue.
- **Evidence:** a 10,000-document HTML-elements benchmark grouped source hashes into the table's
  64 DuckLake buckets and used at most eight concurrent insert statements. After 9,125,303 rows
  and 463 MB of active Parquet data had committed, DuckLake exposed 3,038 active data files. The
  client had stopped using CPU and network for more than five minutes while the combined
  write-and-instrumentation operation had not returned; the run did not isolate whether the
  outstanding Quack query was a write or the file enumeration. A 100-document smoke run completed
  successfully with 72,461 rows, 19.5 MB of Arrow input, and 58 files. A later bounded rebuild
  isolated the phases: a 299-document, 103.7 MB projected batch used four bucket-aligned writers
  and spent 190.7 seconds in writes while Atlas and the single Quack replica remained lightly
  utilized. Nested client timings showed transaction close was small; streamed inserts into the
  link-pair and link-occurrence targets dominated, with substantial per-statement tail variance.
  Coalescing both targets into one transaction and aligning retry probes with the physical bucket
  keys removed Atlas-side commit chatter and full scans, but did not remove the streamed-insert
  latency. Three clean Ethernet samples improved normalized write time by about 12% over recent
  Wi-Fi batches, yet still took 174--195 seconds to write roughly 96--107 MB. A subsequent fresh
  batch left one remote write unacknowledged beyond the 300-second watchdog; restarting reused its
  committed slices and completed the retry without duplicate output.
- **Needed upstream contract:** one streamed Arrow/Parquet bulk insert must remain one continuous
  server-side DuckDB sink per target partition, with a bounded file count and an unambiguous
  completion acknowledgement. Alternatively, expose a managed upload-and-register primitive that
  accepts validated Parquet bytes, places them in lake-owned storage, and atomically registers
  them with DuckLake.
- **Atlas status:** Atlas coalesces and bucket-groups local projection work, but does not claim that
  larger Arrow batches control final lake file sizes. Basin remains responsible for compaction;
  the benchmark keeps file-count measurement off the latency-sensitive CDC path.

## DuckBasin bulk-commit beta advertises an unsigned upload header

- **Atlas caller:** raw HTTP bulk-commit pilot for append-only fixed projections.
- **Evidence:** operation `37c78505-62f1-4a79-b82a-8ec45ed7342f` prepared a single-file upload
  with both `content-md5` and `x-amz-checksum-sha256` in the returned header map, while the
  presigned URL's `X-Amz-SignedHeaders` covered only `content-md5` and `host`. Sending every
  returned header as required by the pilot contract produced S3
  `403 AccessDenied / HeadersNotSigned: x-amz-checksum-sha256`. Omitting only the unsigned
  checksum header allowed the operation to commit, and repeating prepare and complete returned
  the same operation and snapshot. A subsequent two-table operation
  (`a05405c7-7737-497e-bd4b-6c3656e9ca2d`) atomically exposed 10,000 HTML-element rows and
  10,000 link-occurrence rows in snapshot 14878. A 101.48 MiB operation
  (`dd070a62-fdec-479b-8afd-700a4be7f4e2`) uploaded in 4.19 seconds and committed 1.4 million
  rows in 10.29 seconds end to end; Basin reported 1.782 seconds of validation and 0.698 seconds
  of commit time.
- **Needed upstream contract:** every returned upload header must be covered by the presigned
  request, or Basin must omit headers the client must not send. The documented rule to send every
  returned header must work without client-side inspection of `X-Amz-SignedHeaders`.
- **Atlas status:** fixed upstream in Basin revision `410d840`; SHA-256 is now part of the
  presigned request and the client sends every returned header unchanged. A live replay-safe probe
  committed through the corrected contract before Atlas enabled bulk shadow writes.

## Bulk materialization needs logical-type copy and keyed mutation modes

- **Atlas caller:** fixed document and visit projections, including shadow rebuilds and bounded
  incremental repair.
- **Evidence:** exact registration of an Atlas JSON-LD Parquet file failed because DuckLake
  expected `VARIANT` while `read_parquet` exposed its physical `STRUCT` representation. Basin
  revision `16d00de` added a generic `copy` mode which inserts an unpartitioned staging file
  through the target table inside the same marked DuckLake transaction. A live five-table Atlas
  operation then committed content stats, HTML elements, JSON-LD, links, and link occurrences,
  preserved `VARIANT`, and replayed without duplicate rows. A 100-document production projection
  completed in 25.6 seconds with exact registration and 31.8 seconds with Basin-owned copy/layout,
  compared with roughly 190 seconds spent in the former Quack streamed-write phase.
- **Needed upstream contract:** keep `register` for exact append files and `copy` for logical types
  and Basin-owned layout. Basin revision `c74ac9f` now provides restricted append,
  replace-by-key, and delete-by-key actions over uploaded Parquet sources, with validated
  identifiers and all actions committed under the same operation marker; it does not expose
  arbitrary SQL. Rebuild lifecycle still needs a generic
  create-like-target, atomically activate-generation, and cleanup-retired-tables operation with the
  same durable idempotency and lost-ack recovery.
- **Atlas status:** all five live and shadow document insertions now use one atomic bulk operation
  with at most one uploaded file per table. Document correction deletions use one keyed bulk
  operation. Exact link-rollup refreshes, visit projections, and generation DDL remain on bounded
  trusted SQL while their staged-row and generation lifecycle integrations are completed; Atlas
  will not disguise them as append-only data or introduce a second Atlas-owned lake control plane.

## Public Quack remote SQL can resolve the Basin control catalogue

- **Atlas caller:** trusted server-side table-identity and fixed materialization SQL.
- **Evidence:** server-side metadata enumeration can resolve Basin control catalogue names.
- **Needed upstream contract:** per-lake least-privilege execution unable to resolve Basin control
  relations or another lake's metadata.
- **Atlas status:** Quack remains private, credentials remain server-side, and Atlas accepts no
  user-authored remote SQL.

## DuckLake rejects column comments on views

- **Atlas caller:** public catalogue installation for `web.*` and `dom.*` view documentation.
- **Evidence:** `COMMENT ON VIEW web.page` succeeds, while
  `COMMENT ON COLUMN web.page.page_id` fails remotely with
  `Cannot comment on columns for entry page - it is not a table`.
- **Needed upstream contract:** allow standard DuckDB column comments on persistent DuckLake views
  and expose them through `duckdb_columns()`.
- **Atlas status:** Atlas publishes supported view comments and serves view-column descriptions
  from its validated public catalogue manifest.
