# Upstream DuckLake feedback

Periplus uses the official DuckDB and DuckLake extensions directly. LakeDucktor owns physical lake
maintenance; Periplus owns ingestion evidence and logical materialization generations.

## DuckLake rejects column comments on views

- **Periplus caller:** public `web.*` and `content.*` catalogue documentation.
- **Evidence:** commenting on a view succeeds; commenting on one of its columns fails because the
  entry is not a table.
- **Needed upstream contract:** allow standard column comments on persistent DuckLake views and
  expose them through `duckdb_columns()`.
- **Periplus status:** Periplus publishes supported view comments and serves view-column descriptions
  from its authoritative manifest.

## DuckLake UUID bucket projection can crash during DML

- **Periplus caller:** superseded mutable material page and link projections.
- **Evidence:** DuckDB/DuckLake 1.5.5 on Linux ARM64 exits with `SIGSEGV` while DML writes a table
  partitioned by a UUID `bucket(64, ...)` transform. Captured native stacks include both
  `DuckLakeMergeInsert::Sink` and `DuckLakeMergeUpdate::Sink`, followed by
  `ProjectAndCastForCopy` → `Murmur3ScalarFunction` → `Vector::GetValueInternal`. A filtered
  `INSERT ... SELECT` can trigger the same invalid vector access. Isolated temporary DuckLakes
  reproduce exit 139 without Periplus state, CDC, NATS, Postgres, or LakeDucktor; equivalent
  unpartitioned writes succeed.
- **Needed upstream contract:** UUID bucket transforms must accept dictionary, selection, and
  projection vectors produced by DML without invalid vector access, with regression coverage for
  MERGE insert/update and filtered INSERT inputs.
- **Periplus status:** Periplus removed the affected projections. Semantic material data is immutable
  Parquet registered append-only; no page/head/link replacement DML remains.

## DuckLake Postgres inlined-data reads can invalidate independent readers

- **Periplus caller:** the serialized, process-owned API catalogue connection reading public and
  physical relations while independent ingestor connections commit DuckLake evidence.
- **Evidence:** DuckDB/DuckLake 1.5.5 on Linux ARM64 raised `INTERNAL Error: Attempted to access
  index 0 within vector of size 0` in
  `PostgresMetadataManager::TransformInlinedData` ->
  `DuckLakeInlinedDataReader::TryInitializeScan`. DuckDB then permanently rejected every query on
  that connection with `database has been invalidated`. At the same metadata snapshot, fresh
  attachments successfully read all four public views. The catalogue contained active Postgres
  inlined-data generations and had concurrent commits from independent writer attachments. This is
  consistent with the cross-attachment inlined-data cache invalidation gap tracked in
  [duckdb/ducklake#1305](https://github.com/duckdb/ducklake/issues/1305), although that report's
  surface error is a stale missing-table reference rather than this invalid vector access.
- **Needed upstream contract:** an independently attached reader must see one transactionally
  consistent inlined-data membership and schema after another attachment commits or flushes inline
  data. A metadata-cache miss or stale entry must refresh without an internal error or connection
  invalidation.
- **Periplus status:** the API discards a connection after DuckDB `InternalException` or
  `FatalException`, opens a validated fresh attachment, and retries its serialized read-only
  operation once. This bounds the incident but does not replace an upstream cache-coherence fix.
