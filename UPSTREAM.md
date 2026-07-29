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
  compared with roughly 190 seconds spent in the former Quack streamed-write phase. After the
  complete materialization and compaction fixes, an 800-document HTML-elements benchmark projected
  711,539 rows (175.7 MB of Arrow data) and completed in 100.9 seconds. DOM projection used 81.2
  seconds, while the Basin upload and atomic commit used 17.4 seconds and left exactly 64 active
  files containing 34.4 MB. The committed and acknowledged row counts matched, and Basin removed
  the disposable generation through the same bulk lifecycle path. At this size the local DOM
  projector, not lake upload, is the dominant cost.
- **Needed upstream contract:** keep `register` for exact append files and `copy` for logical types
  and Basin-owned layout. Basin revision `c74ac9f` now provides restricted append,
  replace-by-key, and delete-by-key actions over uploaded Parquet sources, with validated
  identifiers and all actions committed under the same operation marker; it does not expose
  arbitrary SQL. Basin revision `75d2697` adds generic clone, atomic generation swap, and table
  cleanup actions under the same durable operation contract.
- **Atlas status:** all fixed-projection mutations and rebuild lifecycle actions now use Basin bulk
  operations. A live worker-loss test interrupted Atlas after Basin committed a five-table
  document bundle but before Atlas advanced its cursor; restart replayed the identical source
  slice, wrote zero rows, and then advanced safely. Quack remains a read path and is no longer a
  materialization write path.

## Bulk copy can create compaction debt faster than fixed-tier maintenance removes it

- **Atlas caller:** partitioned fixed projections written in bounded rebuild and CDC batches.
- **Evidence:** a live 300-document rebuild projected roughly 87--110 MiB per batch and sustained
  about 4.2 documents per second through three or four concurrent Basin operations. Because
  `copy` honors each table's 64-bucket partition layout, every populated target gained roughly one
  small file per touched bucket per batch. At 2,388 documents, the five shadow tables had
  247--488 active files each, averaging about 0.03--0.22 MiB. Basin correctly discovered and
  compacted the internal shadow tables, but its fixed-tier passes ran about once per table per
  minute and processed only 8--15 files into four while the next document batch added roughly
  50--64 files per populated target. Projection throughput remained flat at this corpus size, so
  this is accumulated physical debt rather than a current write stall. Near the end of the
  14,338-observation rebuild, the five document shadow tables peaked at 1,020--2,024 active files
  each. Basin revision `af07de9` replaced the fixed four-output limit with tier-specific bounds of
  32, 16, 8, and 4 compacted output groups, and kept a table immediately runnable after productive
  maintenance until a fresh inspection found no remaining debt. Revision `459a285` added the
  database default and explicit raw-write initialization required for that durable drain state.
  After deployment, individual T0 passes processed 545--977 files into 32 outputs. All five tables
  reached a healthy 64/97/64/80/68-file layout in about 2.5 minutes, with roughly 3.5 CPU cores and
  452 MiB observed at the sidecar. During subsequent 128 MiB Atlas batches, active file counts
  remained bounded at roughly 64--155 instead of returning to the thousand-file backlog. Basin
  revision `0ced105` then made maintenance admission honor the live cgroup CPU and memory envelope,
  reserved up to 2 GiB per independent compaction connection, and converted transient allocation
  failures into bounded retries. Revision `134175e` made the controller add replacement Quack
  replicas before retiring the serving generation, so maintenance-sidecar and Quack upgrades no
  longer rotate every session at once.

  A later keyed-write overlap probe exposed two further physical-maintenance failures. Before
  revision `5eb14a8`, a Basin bulk replace-by-key operation and automatic compaction could mutate
  the same DuckLake table concurrently. The completed Atlas shadow therefore contained duplicate
  `pages` and inconsistent `page_heads` even though every bulk operation was individually
  idempotent. Basin now shares a sorted per-lake/per-table PostgreSQL advisory-lock contract across
  compaction, bulk file mutations, and table lifecycle actions. A 20-batch live stress test retained
  the real 64-bucket layout, overlapped compaction with 300-row writes and 100-row key replacement,
  and ended with 4,100 rows for 4,100 distinct keys after every intermediate invariant check.
  Contended commit latency rose from 11.9 to 41.5 seconds rather than allowing silent corruption;
  unrelated tables remained concurrent. A subsequent page-only shadow repair processed all 14,338
  visits and 42,829 output rows in 759 seconds while compaction continued, then atomically replaced
  all three page relations. The activated `pages`, `page_heads`, and `page_observations` tables
  contained 12,505/12,505, 12,505/12,505, and 14,338/14,338 rows/distinct keys respectively.

  That same probe ended with 1,039 active files containing only 1.6 MiB. DuckLake had stored 1,900
  small deletions in its per-table inline-delete relation. `ducklake_merge_adjacent_files` excludes
  every file with physical or inlined deletions, while Basin's inspector counted only physical
  delete files; repeated maintenance calls therefore reported `0 files into 0` and incorrectly
  cleared the table as healthy. Basin revisions `bbc60fd` and `9623f37` detect both delete forms,
  expose them as table debt, and schedule a threshold-zero rewrite before tier merging. In the live
  64-bucket canary, Basin rewrote 975 marked files into 64 in 41.7 seconds, drained two bounded merge
  passes in another 12.5 seconds, and finished with 64 files, zero delete blockers, and the unchanged
  4,100-row/4,100-key invariant.
- **Needed upstream contract:** bulk ingestion and automatic maintenance must expose and enforce a
  bounded debt envelope. Compaction admission and worker throughput should scale with files and
  bytes created by bulk operations, including generation tables, and provide an observable
  completion barrier suitable before generation activation. The mechanism must remain generic:
  Atlas should not know DuckLake file paths, run lake maintenance SQL, or special-case Basin's
  compaction tiers. DuckLake's rewrite operation also needs a bounded file, partition, or input-byte
  limit. Today Basin can safely automate threshold-zero blocker rewrites only when the table's full
  active data-file footprint fits one maintenance connection's memory budget; an arbitrarily large
  mutable table can otherwise remain visible as deferred debt even when only a bounded subset of
  files contains deletions.
- **Atlas status:** fixed upstream in Basin revisions `af07de9` and `459a285`; Atlas retains its
  `64`-bucket production layouts. Revisions `0ced105`, `5eb14a8`, `bbc60fd`, and `9623f37` close
  resource-admission, write/maintenance serialization, and inline-delete planning gaps without an
  Atlas-specific maintenance path. The 14,338-observation live rebuild proved both bounded catch-up
  after a large backlog and bounded steady-state debt while writes continued. Million-document
  readiness still requires the planned larger rebuild benchmark, and huge mutable tables still
  need the bounded DuckLake rewrite primitive, but no schema or partition removal is required for
  the observed failure modes.

## Public Quack remote SQL can resolve the Basin control catalogue

- **Atlas caller:** trusted server-side table-identity and fixed materialization SQL.
- **Evidence:** server-side metadata enumeration can resolve Basin control catalogue names.
- **Needed upstream contract:** per-lake least-privilege execution unable to resolve Basin control
  relations or another lake's metadata.
- **Atlas status:** Quack remains private, credentials remain server-side, and Atlas accepts no
  user-authored remote SQL.

## Managed Quack has no generic product-extension loading contract

- **Atlas caller:** the standards-shaped `dom.query_selector` and `dom.query_selector_all`
  wrappers backed by the Atlas DuckDB extension.
- **Evidence:** the pinned native extension passes 108 release assertions and its streaming
  selector returned the expected Common Crawl links from the live lake in 6.0 seconds through the
  direct read-only development attachment. The managed Quack sessions do not load that extension,
  so Atlas setup correctly leaves both wrappers absent rather than publishing functions that every
  managed query would fail to bind. Loading the extension only in a downstream client is too late:
  the persistent wrappers are installed by the server-side setup connection, and the direct lake
  boundary is intentionally read-only.
- **Needed upstream contract:** let an operator configure an allowlisted, checksummed set of
  DuckDB extension artifacts for managed runtimes; validate each artifact against the pinned DuckDB
  ABI, load it before readiness and session minting, expose the loaded name/version fingerprint,
  and roll replicas safely when the set changes. This must be a generic Quack capability rather
  than an Atlas-specific image fork.
- **Atlas status:** portable `web.*`, `dom.elements`, `dom.get_attribute`, and `dom.text_content`
  remain available. Native selector execution is proven against the lake, but the public
  standards-shaped selector surface is not production-ready until managed extension loading exists.

## DuckLake rejects column comments on views

- **Atlas caller:** public catalogue installation for `web.*` and `dom.*` view documentation.
- **Evidence:** `COMMENT ON VIEW web.page` succeeds, while
  `COMMENT ON COLUMN web.page.page_id` fails remotely with
  `Cannot comment on columns for entry page - it is not a table`.
- **Needed upstream contract:** allow standard DuckDB column comments on persistent DuckLake views
  and expose them through `duckdb_columns()`.
- **Atlas status:** Atlas publishes supported view comments and serves view-column descriptions
  from its validated public catalogue manifest.
