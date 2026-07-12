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

### Remote DuckLake connection support for `ducklake-client`

- **Atlas caller:** every Atlas service when DuckDB is hosted by a dedicated stateful Quack
  service.
- **Evidence:** with DuckDB 1.5.4, a Quack client can execute
  `remote.query('SELECT ... FROM atlas.main.documents')`, but the normal catalogue path would
  require the unrepresentable four-part name `remote.atlas.main.documents`. `SHOW ALL TABLES` on
  the Quack attachment does not expose the server's attached DuckLake as client tables. Serving
  the DuckLake as the active database does not change this. A server-side view in the primary
  DuckDB database *is* exposed as `remote.serving.documents` and accepts bound parameters.
- **Smallest useful upstream contract:** a `ducklake-client` remote connection that targets a
  Quack serving schema while preserving result streaming, parameter binding, transactions, and
  bulk execution (`executemany`), and the schema/snapshot/maintenance operations needed by
  authorized callers. The dedicated server, not an Atlas client process, owns the DuckLake
  attachment.
- **Additional evidence:** crawl materialization fan-out planning needs to insert a frozen member
  set atomically. Atlas's temporary `QuackConnection` bridge had to render repeated bound writes
  into one remote transaction script because the remote connection surface has no bulk-execution
  primitive.
- **Atlas status:** feasible but not transparently supported. The repository worker remains the
  sole application authority requesting writes but executes them as a Quack client. Atlas should
  not add SQL literal interpolation around `remote.query(...)`.

### Preserve attached-catalogue identity in Quack scans

- **Atlas caller:** every Atlas Quack client using a platform-assigned DuckLake schema such as
  `remote.atlas.documents`.
- **Evidence:** Quack 1.5.4 discovers the unique DuckLake schema `atlas` and exposes it in the
  client catalogue, but a scan of `remote.atlas.documents` is sent to the server as unqualified
  `FROM documents` and fails. `QuackSchemaSet` records the source catalogue but notes that duplicate
  schema names collide; `QuackTableCatalogEntry::GetScanFunction` forwards only `table_name`.
  Setting `USE ducklake.atlas` before `quack_serve` does not affect fresh request connections, and
  DuckDB rejects setting `schema` globally.
- **Smallest useful upstream contract:** preserve source catalogue and schema on loaded Quack
  entries and send fully qualified scan/DML targets, or allow one attached catalogue to be exposed
  as the endpoint root. A client must be able to read/write `remote.atlas.documents` with bound
  parameters when it maps to `ducklake.atlas.documents` on the server.
- **Upstream:** [Quack PR #213](https://github.com/duckdb/duckdb-quack/pull/213).
- **Atlas status:** blocked until the change is merged and deployed. Views, aliases, dynamic SQL,
  and custom RPC functions are rejected as compatibility shims. The Compose service is a working
  reproduction environment. `remote.atlas.documents` is not production-ready before adoption.

### Remote connection contract for `ducklake-cdc-client`

- **Atlas caller:** live materialization consumers when the DuckLake execution process is remote.
- **Evidence:** `CDCClient(..., install_extension=False)` and `DMLConsumer` successfully created a
  consumer, read an insert, and committed its cursor through a connection adapter whose
  `execute(sql)` delegates to `remote.query(?)`. The server loaded the local DuckDB 1.5.4 macOS
  arm64 `ducklake_cdc` artifact with `allow_unsigned_extensions=true`.
- **Smallest useful upstream contract:** document and test the structural connection/lake protocol
  accepted by `CDCClient`, including remote adapters and the requirement that a consumer's calls
  remain pinned to one logical Quack connection for lease ownership.
- **Atlas status:** adopted for the CDC path. Compose builds the matching immutable Linux artifact
  from `ducklake-cdc` v0.5.3 into each image architecture. Atlas keeps each consumer on its existing
  logical Quack connection because leases are connection-scoped.

## In progress

### Typed bound parameters in Quack remote queries

- **Atlas caller:** every remote catalogue operation with application values.
- **Upstream:** [Quack PR #214](https://github.com/duckdb/duckdb-quack/pull/214).
- **Atlas status:** blocked until the change is merged and deployed. Atlas must not substitute SQL
  literals as a workaround.

## Released, awaiting Atlas adoption

No releases awaiting adoption. Atlas adopted `ducklake-cdc-client` v0.6.0 for typed retryable lease
failures and bounded cancellation-aware close/release, and `ducklake-cdc` v0.5.3 from its immutable
release assets. The image verifies the published SHA-256 and Atlas validates both `cdc_version()`
and `cdc_build_revision()` at startup. Community installation remains disabled until
[community-extensions PR #2229](https://github.com/duckdb/community-extensions/pull/2229) is merged.
