# Upstream DuckLake Feedback

This is Atlas's focused wishlist and issue log for the DuckLake libraries maintained alongside it.
Atlas intentionally dogfoods these packages, so friction found here should improve the shared
library instead of becoming a permanent Atlas-specific workaround.

## Libraries

| Package or extension | Atlas role | Local source |
| --- | --- | --- |
| `ducklake-client` | Typed DuckLake configuration, attachment, schema, transactions, and catalogue access | `/Users/ekku/Code/quack/ducklake-python-client` |
| `ducklake-cdc` | Durable DDL/DML change consumption for publication tables | `/Users/ekku/Code/quack/ducklake-cdc-extension` |
| `ducklake-cdc-client` | Python consumer API for publication CDC, replay, and bootstrap | `/Users/ekku/Code/quack/ducklake-cdc-python-client` |

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

### Persisted table macros cannot round-trip optional NULL defaults

- **Former Atlas caller:** fixture-seeded selector-history table macros.
- **Evidence:** DuckDB accepts `CREATE MACRO example(required, optional := NULL)`, but committing
  the macro to DuckLake fails with `unsupported type "NULL"`. Using
  `optional := CAST(NULL AS VARCHAR)` commits, but reopening the catalogue substitutes the string
  `"NULL"`; a body cast such as `CAST(optional AS BIGINT)` then raises a conversion error when the
  optional argument is omitted.
- **Smallest useful upstream contract:** DuckLake should persist and restore typed nullable macro
  defaults without changing their value, with a regression that invokes the macro both with and
  without the optional argument after reopening the catalogue.
- **Atlas status:** the selector-history fixtures were superseded by the required-parameter
  `macros.suggest_records` and `macros.extract_records` helpers. Atlas has no active caller needing
  optional macro defaults and carries no workaround; the persistence gap remains valid upstream.

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
- **Upstream status:** `ducklake-cdc-client` 0.6.1 adds bounded close/cancellation, typed retryable
  lease errors, broader H-022 retry recognition, and restart guidance. Together with
  `ducklake-cdc` 0.5.4, normal close uses owner-token-conditional release. These changes resolve
  shutdown and stale-owner safety, but do not establish that `listen` leaves no PostgreSQL
  transaction open between successful bounded polls.
- **Atlas status:** uses one consumer-owned derived DuckDB connection and
  `read(max_snapshots=100)` plus an async one-second wait, with bounded/reaped embedded PostgreSQL
  pools. The remaining `listen` behavior is not on Atlas's active path but remains worth a focused
  upstream reproduction.

Atlas deliberately embeds DuckDB in each ingestion and materialization worker. Remote Quack support
is not an Atlas deployment direction and is not tracked as an Atlas upstream requirement.

## In progress

No upstream work is currently in progress.

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

The CDC releases are fully adopted: Atlas uses `ducklake-cdc-client` 0.6.1 and installs
`ducklake_cdc` 0.5.4 from the DuckDB community repository for DuckDB 1.5.4. Atlas validates
`cdc_version()` at startup. The crawl CDC planner lets each high-level consumer derive and own its
dedicated DuckDB connection, and its supervisor reopens a fresh consumer after typed retryable
failures. The underlying H-022 lock-ordering defect remains tracked upstream.
