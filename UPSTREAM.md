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

### DuckLake crash when replacing an update-fragmented table from itself

- **Atlas caller:** deployment-time repartition of retained crawl materialization fan-out state.
- **Evidence:** DuckLake 1.5 exited with signal 11 while one transaction created a bucketed
  replacement, ran `INSERT INTO replacement SELECT * FROM source`, dropped the source, and renamed
  the replacement. The source contained 1,378 current rows across 597 small data/update fragments.
  Reading the same rows to Arrow succeeds, and registering that Arrow table before the replacement
  transaction makes the operation complete reliably.
- **Smallest useful upstream contract:** replacing a table from a scan of its current snapshot must
  not crash when the source has update fragments; add a regression covering scan, drop, and rename
  in one DuckLake transaction.
- **Atlas status:** mitigated by staging only the small fan-out state tables in memory before their
  one-time bucket-layout rewrite. The 1.9-million-row append-only elements table continues to use a
  direct transactional `INSERT ... SELECT` so Atlas does not introduce an unbounded memory copy.

### Cancellation and transaction semantics for blocking CDC listen

- **Atlas caller:** live materialization planning in materialization workers through
  `ducklake-cdc-client`.
- **Evidence:** a materialization worker using `DMLConsumer.listen(timeout_ms=1000)` retained a PostgreSQL
  transaction in `idle in transaction` for more than five minutes; a native stack showed
  `WaitForNextSnapshotWithSubscriptions`. During the same run, the catalogue exhausted PostgreSQL
  clients. The timeout bounded the Python call's intended wait but did not prevent the underlying
  metadata transaction from remaining open.
- **Smallest useful upstream contract:** `listen` must leave no transaction open between bounded
  polls, with a regression test against a Postgres-backed DuckLake; alternatively document it as a
  blocking-only primitive and provide an async/non-blocking polling helper with explicit lease
  lifecycle semantics.
- **Atlas status:** mitigated locally by using `read(max_snapshots=100)` plus an async one-second
  wait and by bounding/reaping every embedded Postgres pool. The upstream behavior is still worth a
  focused reproduction because other consumers may reasonably choose `listen`.

Atlas deliberately embeds DuckDB in each ingestion and materialization worker. Remote Quack support
is not an Atlas deployment direction and is not tracked as an Atlas upstream requirement.

## In progress

No upstream work is currently in progress.

## Released, awaiting Atlas adoption

No releases awaiting adoption. Atlas adopted `ducklake-cdc-client` v0.6.0 for typed retryable lease
failures and bounded cancellation-aware close/release, and `ducklake-cdc` v0.5.3 from its immutable
release assets. The image verifies the published SHA-256 and Atlas validates both `cdc_version()`
and `cdc_build_revision()` at startup. Community installation remains disabled until
[community-extensions PR #2229](https://github.com/duckdb/community-extensions/pull/2229) is merged.
