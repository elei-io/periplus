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

## Public Quack remote SQL can resolve the Basin control catalogue

- **Atlas caller:** trusted server-side table-identity and fixed materialization SQL.
- **Evidence:** server-side metadata enumeration can resolve Basin control catalogue names.
- **Needed upstream contract:** per-lake least-privilege execution unable to resolve Basin control
  relations or another lake's metadata.
- **Atlas status:** Quack remains private, credentials remain server-side, and Atlas accepts no
  user-authored remote SQL.
