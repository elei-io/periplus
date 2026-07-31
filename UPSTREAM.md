# Upstream DuckLake feedback

Atlas uses the official DuckDB and DuckLake extensions directly. LakeDucktor owns physical lake
maintenance; Atlas owns ingestion evidence and logical materialization generations.

## DuckLake rejects column comments on views

- **Atlas caller:** public `web.*` and `dom.*` catalogue documentation.
- **Evidence:** commenting on a view succeeds; commenting on one of its columns fails because the
  entry is not a table.
- **Needed upstream contract:** allow standard column comments on persistent DuckLake views and
  expose them through `duckdb_columns()`.
- **Atlas status:** Atlas publishes supported view comments and serves view-column descriptions
  from its authoritative manifest.

## DuckLake UUID bucket projection can crash during DML

- **Atlas caller:** superseded mutable material page and link projections.
- **Evidence:** DuckDB/DuckLake 1.5.5 on Linux ARM64 exits with `SIGSEGV` while DML writes a table
  partitioned by a UUID `bucket(64, ...)` transform. Captured native stacks include both
  `DuckLakeMergeInsert::Sink` and `DuckLakeMergeUpdate::Sink`, followed by
  `ProjectAndCastForCopy` → `Murmur3ScalarFunction` → `Vector::GetValueInternal`. A filtered
  `INSERT ... SELECT` can trigger the same invalid vector access. Isolated temporary DuckLakes
  reproduce exit 139 without Atlas state, CDC, NATS, Postgres, or LakeDucktor; equivalent
  unpartitioned writes succeed.
- **Needed upstream contract:** UUID bucket transforms must accept dictionary, selection, and
  projection vectors produced by DML without invalid vector access, with regression coverage for
  MERGE insert/update and filtered INSERT inputs.
- **Atlas status:** Atlas removed the affected projections. Semantic material data is immutable
  Parquet registered append-only; no page/head/link replacement DML remains.
