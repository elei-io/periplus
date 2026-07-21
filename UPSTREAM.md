# Upstream DuckLake and Quack Feedback

This is Atlas's focused wishlist and issue log for the DuckLake libraries maintained alongside it.
Atlas intentionally dogfoods these packages, so friction found here should improve the shared
library instead of becoming a permanent Atlas-specific workaround.

## Libraries

| Package or extension | Atlas role | Local source |
| --- | --- | --- |
| `ducklake-client` | Typed DuckLake configuration, attachment, schema, transactions, and catalogue access | `/Users/ekku/Code/quack/ducklake-python-client` |
| `ducklake-cdc` | Durable DDL/DML change consumption for publication tables | `/Users/ekku/Code/quack/ducklake-cdc-extension` |
| `ducklake-cdc-client` | Python consumer API for publication CDC, replay, and bootstrap | `/Users/ekku/Code/quack/ducklake-cdc-python-client` |
| `quack` | Browser-to-server analytical DuckDB transport | `/Users/ekku/Code/quack/duckdb-quack` |

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

## In progress

No upstream work is currently in progress.

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

The CDC releases are fully adopted: Atlas uses `ducklake-cdc-client` 0.7.0 and temporarily pins
the checksummed `ducklake_cdc` 0.6.1 release artifact for DuckDB 1.5.4 while
[community PR #2280](https://github.com/duckdb/community-extensions/pull/2280) lands. Atlas validates
`cdc_version()` at startup. The catalogue relay owns exactly one catalogue-wide DML tick cursor
and one catalogue-wide DDL cursor; table-specific fan-out happens in NATS. Other high-level
consumers derive and own dedicated DuckDB connections, and supervisors reopen a fresh consumer
after typed retryable failures. The underlying H-022 lock-ordering defect remains tracked
upstream.
