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
