# Upstream DuckLake and Quack feedback

Atlas uses the official DuckDB Python library, session-affine Quack attachments minted by
DuckBasin, and Basin-published JetStream CDC. This file contains only active upstream findings.
Resolved and retired-client investigations remain available in version control.

## Quack replica startup mutates DuckLake history

- **Atlas caller:** horizontally routed clients minted for ingestion, materialization, and
  catalogue queries.
- **Evidence:** a 330.8-second read-only load run issued 7,225 remote `SELECT` operations while
  Basin scaled from zero to six desired replicas. DuckLake advanced from snapshot 2 to 10, with
  each new snapshot replacing `main.duckbasin_access_policy`. Each replica currently runs
  `CREATE OR REPLACE VIEW duckbasin_access_policy` during startup.
- **Smallest useful upstream contract:** replica startup must not persist per-replica catalogue
  DDL. Add a scale-from-zero regression proving read-only load does not advance the lake.
- **Atlas status:** no workaround. Atlas ignores policy-view-only snapshots as application DDL.

## Quack remote scans drop non-main schema qualification

- **Atlas caller:** repository, ingestion, and materialization SQL against `main`, `_atlas`, and
  `_atlas_materializations`.
- **Evidence:** `DESCRIBE nl.train_stations` succeeded against `atlas_test`, but
  `SELECT count(*) FROM nl.train_stations` generated an unqualified server scan of
  `train_stations`. Explicit remote SQL returned 578 rows.
- **Smallest useful upstream contract:** preserve catalogue and schema qualification in generated
  remote scans, including two identically named tables in different schemas.
- **Atlas status:** explicitly accepted for the initial managed deployment. Atlas retains
  qualified SQL and will consume the upstream fix without an application rewrite.

## Long Quack mutations replay before query-completion acknowledgement

- **Atlas caller:** initial materialization bootstrap through
  `CREATE TABLE ... AS SELECT ...` inside a session-affine remote transaction.
- **Evidence:** with every Atlas materialization worker stopped, one client issued a CTAS into a
  never-before-used `_atlas_materializations.page_links_probe` name. After about 50 seconds Quack
  returned `Table with name "page_links_probe" already exists`; rollback left no table. The same
  reproduction occurred through both horizontal and single-replica endpoints. Replacing CTAS with
  `CREATE OR REPLACE TABLE` did not repair the contract: the eventual error was
  `cannot rollback - no transaction is active`, again with no committed table. The installed Quack
  extension reports version `40de7ba` and does not recognize `quack_enable_reconnects`.
- **Smallest useful upstream contract:** ship the query-completion acknowledgement/reconnect work
  merged in duckdb-quack PR 224, enable it on both ends, and prove one long transactional DDL
  statement executes exactly once and retains the same server connection through `COMMIT`.
- **Atlas status:** no SQL-level workaround. `OR REPLACE` can hide the duplicate execution while
  still losing the transaction and snapshot fence. Materialization bootstrap remains blocked on
  upgrading and validating Quack.

## Public Quack remote SQL can resolve the Basin control catalogue

- **Atlas caller:** trusted, server-side clients attached to one Basin lake, including the narrow
  metadata lookup that maps Basin DML table IDs to stable DuckLake table UUIDs.
- **Evidence:** `duckdb_databases()` exposed the server-side `control` Postgres catalogue.
  Read-only relation enumeration exposed control-plane table names and metadata schemas for other
  lakes available to the service account. The probe stopped at names; no control-plane rows were
  read.
- **Smallest useful upstream contract:** execute externally supplied remote SQL inside a
  per-lake, least-privilege boundary that cannot resolve Basin control relations or another lake's
  metadata. Add negative tests for control-catalog and cross-lake access.
- **Atlas status:** explicitly accepted for trusted private deployment. Quack remains private,
  credentials remain server-side, and Atlas never passes user-authored SQL to the public remote
  macro. A per-lake restricted database role or isolated internal connection is required upstream.

## Selective joins do not propagate DuckLake partition pruning

- **Atlas caller:** crawl-scoped `views.page_links` materialization over partitioned
  `main.elements`.
- **Evidence:** an outer `crawl_id` predicate and a one-row join on immutable `document_id` did not
  prune element scans. Binding `document_id` directly on each scan did.
- **Smallest useful upstream contract:** propagate constant equality constraints through one-row
  joins and safe reused CTEs into DuckLake partition pruning, covered by JSON-plan regressions.
- **Atlas status:** current fixtures bind the known crawl/document scope directly. An optimizer fix
  would remove those source-specific predicates.

## Persisted table macros cannot round-trip optional NULL defaults

- **Atlas caller:** fixture-seeded `macros.query_selector` and
  `macros.query_selector_all`.
- **Evidence:** `optional := NULL` fails to persist with `unsupported type "NULL"`.
  `CAST(NULL AS VARCHAR)` persists but reopens as the string `"NULL"`.
- **Smallest useful upstream contract:** persist and restore typed nullable macro defaults without
  changing their value, and invoke the reopened macro with and without its optional argument.
- **Atlas status:** Atlas uses an impossible empty-document-ID sentinel and converts it to `NULL`
  inside the macro. A DuckLake fix would remove the sentinel.

## Quack lacks a lake-scoped physical-metadata snapshot for compilation

- **Atlas caller:** the catalogue SQL compiler's partition-aware planning and scan/byte estimates.
- **Evidence:** the public `ducklake_table_info(catalog)` function exposes stable table identity,
  file count, and total file bytes, but not partition columns, partition transforms, table/column
  statistics, or the metadata revision that makes those facts coherent with `duckdb_views()` and
  `duckdb_functions()`. The missing facts exist in DuckLake metadata tables such as
  `ducklake_partition_column`, `ducklake_table_stats`, and `ducklake_table_column_stats`, but Atlas
  intentionally has no metadata-database credentials and must not reach through Quack's lake
  isolation boundary.
- **Smallest useful upstream contract:** expose one read-only, lake-scoped metadata function that
  returns a revision plus stable table UUIDs, schemas, columns, current partition transforms, table
  cardinality, file count/bytes, and available column statistics from the same snapshot. Add a
  concurrent-DDL test proving definitions and physical facts share that revision.
- **Atlas status:** the compiler accepts and tests an immutable `CatalogueMetadataSnapshot`, keeps
  bound values separate from authored SQL, and declines physical estimates when facts are absent.
  Production loading of partition transforms and statistics remains blocked on the upstream
  primitive; Atlas will not query DuckLake's private metadata database directly.
