# Upstream DuckLake and Quack Feedback

This is Atlas's focused wishlist and issue log for its managed DuckLake boundary. Current Atlas
code uses the official DuckDB Python library, a session-affine Quack attachment minted by
DuckBasin, and Basin-published JetStream CDC. Older client and extension findings remain below as
historical upstream evidence, not as active Atlas dependencies.

## Libraries

| Package or extension | Atlas role | Local source |
| --- | --- | --- |
| `ducklake-client` | Retired Atlas dependency; historical findings only | `/Users/ekku/Code/quack/ducklake-python-client` |
| `ducklake-cdc` | Retired Atlas dependency; DuckBasin owns CDC | `/Users/ekku/Code/quack/ducklake-cdc-extension` |
| `ducklake-cdc-client` | Retired Atlas dependency; historical findings only | `/Users/ekku/Code/quack/ducklake-cdc-python-client` |
| `quack` | Managed remote DuckDB transport | `/Users/ekku/Code/quack/duckdb-quack` |
| `DuckBasin` | Managed DuckLake control plane and Quack compute | `/Users/ekku/Code/DuckBasin` |

## How to add an item

Keep requests concrete and evidence-based. Include:

- the affected library;
- the Atlas caller and use case;
- the observed behavior or missing capability;
- the smallest useful upstream contract;
- a reproduction, test, benchmark, or relevant source location;
- whether Atlas is blocked or has temporary local code;
- the upstream issue, pull request, release, and Atlas adoption status when known.

Prefer extending an existing item over creating duplicates. Remove completed items after Atlas uses
the published release; version control retains the history.

## Wishlist

### Basin CDC streams for the `atlas` lake do not receive new snapshots

- **Atlas caller:** the catalogue ingress, which durably consumes the selected lake's global DDL
  and DML-tick streams before publishing Atlas-owned catalogue events.
- **Evidence:** both `BASIN_CDC_ATLAS_LAKE_DDL` and `BASIN_CDC_ATLAS_LAKE_DML_TICKS` exist, but
  fresh real DDL/DML and DuckLake snapshots through 102 produced zero messages and sequence zero in
  both. A dedicated disposable table create plus transactional insert also produced no message
  after 15 seconds. The same service-account and NATS credentials consumed retained, correctly
  shaped DDL and DML payloads from `atlas_test`. The service account cannot inspect
  `/api/cdc/pipelines/` because feed status is platform-administrator-only, so the operator must
  inspect `atlas_lake_ddl` and `atlas_lake_dml_ticks` with `.cdc status`.
- **Smallest useful upstream contract:** provisioning a lake for CDC must make every committed
  DuckLake DDL/DML snapshot appear in its configured JetStream stream, with observable sink health
  and a production smoke test that writes one table and one row then consumes both event types.
- **Atlas status:** ingress transport, durable consumption, payload parsing, and downstream fan-out
  are implemented. End-to-end crawl/materialization verification is blocked until CDC production
  is enabled or repaired for `atlas`.

### Quack replica startup mutates DuckLake history

- **Atlas caller:** horizontally routed, session-affine DuckDB clients minted for ingestion,
  materialization, and catalogue queries against the Basin-managed `atlas` lake.
- **Evidence:** a 330.8-second read-only load run issued 7,225 remote `SELECT` operations while
  Basin scaled from zero to six desired Quack replicas. Across the run and its read-only evidence
  pass, DuckLake snapshots advanced from 2 to 10; every new snapshot contained only
  `views_dropped` and `views_created` for `main.duckbasin_access_policy`. Each Quack replica
  executes `CREATE OR REPLACE VIEW duckbasin_access_policy` during startup in
  `/Users/ekku/Code/DuckBasin/ducklake/runtime/quack.py`.
- **Smallest useful upstream contract:** replica startup must not persist per-replica catalogue
  DDL. Serve the authorization policy from a non-DuckLake/session-local object, or provision the
  shared persistent view idempotently outside replica startup. Add a scale-from-zero regression
  asserting that read-only load does not advance the DuckLake snapshot.
- **Atlas status:** the new minter and load path are read-only and need no workaround. Basin owns
  the extra snapshots; Atlas should not interpret these policy-view-only snapshots as application
  DDL.

### Quack remote scans drop non-main schema qualification

- **Atlas caller:** repository, ingestion, and materialization SQL executed through a
  session-affine Quack attachment to a Basin-managed lake. Atlas uses `main`, `_atlas`, and
  `_atlas_materializations`, so schema qualification is part of the physical table identity.
- **Evidence:** `DESCRIBE nl.train_stations` succeeded against the `atlas_test` lake, but
  `SELECT count(*) FROM nl.train_stations` failed on the server because the generated scan queried
  unqualified `train_stations`; the error itself suggested `nl.train_stations`. Executing the same
  count as explicit remote SQL through the catalog's `query(...)` macro returned 578 rows. In
  Quack's `QuackTableCatalogEntry::GetScanFunction`, scan binding currently records the table name
  without its parent schema.
- **Smallest useful upstream contract:** preserve catalog and schema qualification when Quack
  generates a remote table scan. Add regressions that scan a non-main table and two identically
  named tables in different schemas through an attached Quack catalog.
- **Atlas status:** explicitly accepted for the initial managed deployment despite Atlas's
  non-main schemas. Atlas uses qualified SQL and must not add a permanent `query(...)` rewrite;
  consume the upstream fix directly.

### Public Quack remote SQL can resolve the Basin control catalog

- **Atlas caller:** service-account-authenticated DuckDB clients attached to one Basin lake through
  Quack, including the narrow native-metadata lookup needed to translate Basin DML table IDs into
  Atlas table UUID subjects.
- **Evidence:** from an `atlas_test` attachment,
  `query('SELECT database_name, type FROM duckdb_databases()')` exposed the server-side `control`
  Postgres catalog. Read-only `duckdb_tables()` enumeration then exposed authentication and
  control-plane table names plus DuckLake metadata schemas for all three lakes granted to the test
  service account. The probe stopped at catalog and table-name enumeration; no control-plane rows
  were queried.
- **Smallest useful upstream contract:** externally supplied remote SQL must execute in a
  per-lake, least-privilege database boundary that cannot resolve or read Basin control-plane
  relations or another lake's metadata merely because they are attached in the server process.
  Prefer a dedicated per-lake PostgreSQL role or a separate internal connection over SQL text
  filtering. Add negative tests for `control.*`, catalog introspection, and cross-lake metadata
  access through both `query(...)` and ordinary attached scans.
- **Atlas status:** explicitly accepted for current private compatibility testing, not considered
  resolved. Until the boundary is fixed, keep Quack inaccessible to untrusted callers, never pass
  user-authored SQL to `query(...)`, and treat compromise of the Atlas Quack credential as possible
  exposure of the Basin control plane.

### Scoped DuckLake scans do not inherit selective join predicates

- **Atlas caller:** crawl-scoped `views.page_links` materialization over the partitioned
  `main.elements` table.
- **Evidence:** wrapping the source query with `WHERE crawl_id = $crawl_id` left the DuckLake plan
  scanning every crawl and roughly two million elements three times. Adding the crawl predicate
  inside the reused CTE pruned `crawls`, but joining its one `document_id` to bucket-partitioned
  `elements` still did not produce a document filter on the DuckLake scans. Only binding the
  immutable document ID directly on each element scan enabled partition pruning.
- **Smallest useful upstream contract:** propagate constant equality constraints through a
  one-row join into DuckLake partition pruning, and push an outer scope predicate through reused
  CTEs when doing so is semantically safe. Cover both with `EXPLAIN (FORMAT JSON)` regressions over
  a bucket-partitioned table.
- **Atlas status:** scope jobs now retain the crawl change's document identity and expose explicit
  crawl/document session bindings to materialization SQL. `page_links` filters a single
  materialized element CTE directly. A published optimizer fix would let ordinary scoped views
  obtain the same pruning without Atlas-specific source predicates.

### Typed snapshot-pinned attachment configuration

- **Atlas caller:** crawl-graph edges that join the current `edge.page_links` Arrow package to
  historical catalogue tables at the graph run's frozen pre-run snapshot.
- **Evidence:** DuckLake supports `ATTACH ... (SNAPSHOT_VERSION n)`, but
  `ducklake_client.DuckLakeAttachConfig` has no `snapshot_version` or `snapshot_time` field. Atlas
  can discover a snapshot through `SnapshotsModule.latest()` but cannot open the typed client at
  that snapshot.
- **Smallest useful upstream contract:** add mutually exclusive typed `snapshot_version: int | None`
  and `snapshot_time` attachment options, render them in the generated `ATTACH`, and test a
  read-only client that consistently reads several joined tables from the chosen snapshot.
- **Atlas status:** graph runs durably pin the snapshot ID. Edge execution temporarily creates
  bounded connection-local views using `AT (VERSION => n)` for each catalogue source before
  executing the join. A published typed attachment option would make the whole attached namespace
  snapshot-consistent and remove that per-query source rewrite.

### Persisted table macros cannot round-trip optional NULL defaults

- **Atlas caller:** fixture-seeded `macros.query_selector` and
  `macros.query_selector_all` table macros, whose document scope is optional.
- **Evidence:** DuckDB accepts `CREATE MACRO example(required, optional := NULL)`, but committing
  the macro to DuckLake fails with `unsupported type "NULL"`. Using
  `optional := CAST(NULL AS VARCHAR)` commits, but reopening the catalogue substitutes the string
  `"NULL"`; a body cast such as `CAST(optional AS BIGINT)` then raises a conversion error when the
  optional argument is omitted.
- **Smallest useful upstream contract:** DuckLake should persist and restore typed nullable macro
  defaults without changing their value, with a regression that invokes the macro both with and
  without the optional argument after reopening the catalogue.
- **Atlas status:** Atlas uses an empty-string default and converts it to `NULL` inside the selector
  macro. Content-addressed document IDs cannot be empty, so this preserves the public optional
  argument without changing valid scope semantics. A published fix would let Atlas express the
  default directly as typed SQL `NULL` and delete the sentinel.

### DuckLake crash when replacing an update-fragmented table from itself

- **Former Atlas caller:** deployment-time repartition of the superseded crawl-materialization
  fan-out ledger.
- **Evidence:** DuckLake 1.5 exited with signal 11 while one transaction created a bucketed
  replacement, ran `INSERT INTO replacement SELECT * FROM source`, dropped the source, and renamed
  the replacement. The source contained 1,378 current rows across 597 small data/update fragments.
  Reading the same rows to Arrow succeeds, and registering that Arrow table before the replacement
  transaction makes the operation complete reliably. A second production-shaped reproduction
  segfaulted inside the DuckLake extension on a keyed `UPDATE` of the same fragmented fan-out
  header table; Python's fault handler identified `CrawlMaterializationFanoutStore.refresh()` at
  the update statement, with no concurrent operation on that embedded connection.
- **Smallest useful upstream contract:** replacing a table from a scan of its current snapshot must
  not crash when the source has update fragments; add a regression covering scan, drop, and rename
  in one DuckLake transaction.
- **Atlas status:** the resource-governed materialization contract deletes fan-out headers, members,
  settlement, and their repartition path. The crash remains useful upstream evidence, but Atlas no
  longer relies on a workaround for this table. Large append-only evidence tables continue to use
  direct transactional `INSERT ... SELECT`; Atlas does not introduce unbounded memory copies.

### PostgreSQL transaction semantics for blocking CDC listen

- **Atlas caller:** live materialization planning in materialization workers through
  `ducklake-cdc-client`.
- **Evidence:** a materialization worker using `DMLConsumer.listen(timeout_ms=1000)` retained a PostgreSQL
  transaction in `idle in transaction` for more than five minutes; a native stack showed
  `WaitForNextSnapshotWithSubscriptions`. During the same run, the catalogue exhausted PostgreSQL
  clients. The timeout bounded the Python call's intended wait but did not prevent the underlying
  metadata transaction from remaining open.
- **Smallest useful upstream contract:** `listen` must leave no transaction open between bounded
  polls, with a regression test against a Postgres-backed DuckLake; alternatively document its
  steady-state transaction behavior explicitly.
- **Upstream status:** `ducklake-cdc-client` 0.7.0 adds bounded close/cancellation, typed retryable
  lease errors, broader H-022 retry recognition, and restart guidance. Together with
  `ducklake-cdc` 0.6.0, normal close uses owner-token-conditional release. These changes resolve
  shutdown and stale-owner safety, but do not establish that `listen` leaves no PostgreSQL
  transaction open between successful bounded polls.
- **Atlas status:** the relay uses `read(max_snapshots=100)` on its global DML and DDL connections
  plus an async wait. The remaining `listen` behavior is not on Atlas's active path but remains
  worth a focused upstream reproduction.

### Public DuckLake connection bootstrap statements

- **Atlas caller:** the browser catalogue workbench when DuckDB-Wasm connects directly to a
  platform-provided Quack server and attaches Atlas's configured DuckLake.
- **Evidence:** `ducklake-client` already has the complete typed rendering implementation in
  private `ducklake_client._attach.build_attach_sql`, while public catalogue and storage config
  types expose the remaining setup inputs. Atlas currently has to import that private helper to
  return the authoritative bootstrap statements to its browser client.
- **Smallest useful upstream contract:** expose a public typed function returning the ordered
  connection setup statements (storage secrets followed by DuckLake `ATTACH`) for a
  `CatalogConfig`, `StorageConfig`, alias, and `DuckLakeAttachConfig`.
- **Atlas status:** browser Quack configuration temporarily calls the existing private renderer;
  replace that import once the same contract is published.

### Remote query cancellation

- **Atlas caller:** DuckDB-Wasm catalogue workbench queries executed through a sticky Quack
  attachment.
- **Evidence:** `AsyncDuckDBConnection.cancelSent()` does not stop an active
  `quack_query_by_name` request. The workbench client returns `false` while
  `quack_active_connections()` continues to report the server-side query as `active`. Quack's
  protocol currently has no cancellation message or public server-side cancellation function.
- **Smallest useful upstream contract:** add connection-scoped cancellation that interrupts the
  active server-side DuckDB query and makes the pending client stream terminate with a typed
  cancellation error.
- **Atlas status:** the workbench does not display a cancel action it cannot honor. Queries remain
  read-only, but operator-side Quack limits are still required until cancellation is available.

### Public Wasm client lacks variadic remote bound parameters

- **Atlas caller:** typed workbench-only browser metadata and catalogue-status queries.
- **Evidence:** the local Quack source accepts extra parameters after the SQL argument, but the
  public DuckDB-Wasm 1.5.4 extension exposes only
  `quack_query_by_name(VARCHAR, VARCHAR)`. Passing a third argument fails at bind time.
- **Smallest useful upstream contract:** publish the variadic bound-parameter overload for Wasm
  alongside native clients and cover it with a browser test.
- **Atlas status:** arbitrary workbench SQL remains one bound opaque string. Atlas temporarily
  renders only its own typed workbench metadata/status parameters as escaped SQL literals before
  sending those fixed query templates. Crawl failures and worker backlog no longer use Quack.

## Historical inactive client findings

These entries are retained as useful library evidence. They are not cutover blockers because Atlas
no longer imports `ducklake-client`, `ducklake-cdc-client`, or the `ducklake-cdc` extension.

### `TableInfo.sort_specs` is empty for active DuckLake sort orders

- **Atlas caller:** greenfield catalogue bootstrap validation for declared table sort contracts.
- **Evidence:** DuckLake persists active rows in `ducklake_sort_info` and
  `ducklake_sort_expression` after `ALTER TABLE ... SET SORTED BY`, but
  `ducklake-client` 0.8.2 returns an empty `TableInfo.sort_specs` list for the same table.
- **Smallest useful upstream contract:** make `table.info(...).sort_specs` return the active ordered
  expressions and cover both DuckDB- and Postgres-backed metadata catalogues.
- **Atlas status:** validates the authoritative DuckLake metadata relations directly until the
  typed client result is fixed.

## Released, awaiting Atlas adoption

### Native nested/list types in typed schema definitions

- **Atlas caller:** compact document-quality flag codes in the canonical DuckLake `documents`
  table.
- **Former evidence:** `ColumnDef("VARCHAR[]", nullable=False)` was rejected by the scalar type
  validator even though DuckDB and DuckLake support list columns.
- **Upstream status:** resolved by the published composable `ListType` contract, including nested
  type validation and schema SQL generation. Atlas now locks `ducklake-client` 0.8.2, which contains
  that contract.
- **Atlas adoption:** pending. Atlas still stores the small code vector as JSON once per
  content-addressed document. A direct greenfield schema cutover can replace it with
  `ColumnDef(ListType("VARCHAR"), nullable=False)` and delete the JSON encode/decode path.

Atlas has retired those CDC packages and extension validation. DuckBasin now owns the source
cursors and publishes unchanged CDC payloads to Basin JetStream; Atlas owns only the ingress and
per-table fan-out into its own NATS namespace.
