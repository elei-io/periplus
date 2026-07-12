# Dedicated Quack service implementation plan

## Goal

Run one stateful DuckDB/Quack service as Atlas's DuckLake execution authority. Every Atlas
application process, including the repository worker, connects to it as a Quack client.

```text
Compute layer                                      Stateful layer

API ----------------------------\
runtime workers -----------------+---- Quack ----> DuckDB 1.5.4
repository worker ---------------+                  |-- DuckLake + CDC
materialization worker ----------/                  |-- Postgres catalogue
                                                      `-- S3 data and staging
```

The repository worker remains the only Atlas component authorized to request repository writes.
The Quack service is the only process that physically executes DuckDB and DuckLake reads, writes,
transactions, and maintenance. This distinction preserves Atlas's single-writer application
contract without placing the repository worker on the stateful layer.

## Evidence from the feasibility spike

The 2026-07-12 spike used DuckDB and Quack 1.5.4 with a disposable Postgres-backed DuckLake.

Confirmed:

- Quack served queries against the server's attached DuckLake.
- Server-owned tables and views over DuckLake supported normal prepared parameters and remote
  execution.
- Quack forwarded writes and transactions.
- The local unsigned DuckDB 1.5.4 `ducklake_cdc` binary loaded successfully.
- `CDCClient(..., install_extension=False)` and `DMLConsumer` created a consumer, observed a
  DuckLake insert through Quack, and committed its cursor.
- One long-lived DuckDB process can own the shared memory budget, thread pool, object-store
  connections, and warm storage access near S3.

The spike also found that Quack 1.5.4 is not a transparent connection-string replacement for an
attached DuckLake. `remote.query(...)` reaches the lake, but it accepts server SQL as a string and
is not an acceptable general replacement for Atlas's bound-parameter interface. Atlas must never
render application values into SQL strings as a workaround.

Source inspection and a follow-up test found a deeper limitation. Quack flattens server schemas
from every attached catalogue into the client's remote catalogue, but:

- duplicate schema names across catalogues overwrite one another;
- table scan requests retain only the table name, not the originating server catalogue/schema;
- each server request uses a fresh connection whose default catalogue/schema cannot be set
  globally to the DuckLake attachment.

A unique DuckLake schema therefore appears as `remote.atlas`, but scanning
`remote.atlas.documents` is executed as unqualified `FROM documents` in the server's primary
catalogue and fails. Setting `USE ducklake.atlas` on the connection that starts Quack does not
affect request connections. This is visible in Quack's `quack_schema.cpp` and `quack_table.cpp` and
was reproduced against the Compose service.

The clean target contract remains a platform-provisioned Quack endpoint where Atlas receives a
real `atlas` DuckLake schema as `remote.atlas.*`. Implementing Atlas client migration is blocked
until Quack preserves and uses the originating catalogue/schema for remote scans, or provides a
supported way to expose one attached DuckLake as the endpoint's root catalogue.

## Target ownership

| Component | Owns | Must not own |
| --- | --- | --- |
| Quack service | DuckDB process, DuckLake attachment, extensions, physical query execution, resource limits | Task policy, ingestion decisions, durable queue state |
| Repository worker | Ingestion ordering, validation, idempotency, and sole write authorization | A local DuckDB/DuckLake connection or worker-local durable staging |
| API/runtime workers | Bounded repository reads through Quack | DuckLake credentials, writes, maintenance |
| Materialization worker | Planning and CDC cursor use through Quack | A local DuckDB/DuckLake attachment |
| Postgres catalogue | DuckLake metadata | Analytical data files |
| S3 | DuckLake Parquet and cross-layer staging objects | Control-plane state |

Only the Quack service receives Postgres catalogue and S3 credentials. Client services receive a
Quack endpoint and credentials scoped to their permitted SQL surface.

## Implementation phases

### 1. Create the stateful Quack service

Status: local Compose topology implemented. Start it with:

```sh
docker compose up -d atlas-quack
```

The local endpoint is `quack:127.0.0.1:9494` with token `atlas-local-quack`. Compose mounts the
local Linux arm64 CDC binary read-only; override its host location with
`DUCKLAKE_CDC_EXTENSION_PATH`.

- Add one Compose service and one production process entry point dedicated to DuckDB/Quack.
- Initialize DuckDB 1.5.4 with explicit thread, memory, temporary-directory, and maximum temporary
  storage settings.
- Load Quack, DuckLake, Postgres, object-storage, and `ducklake_cdc` extensions before accepting
  traffic.
- Attach the existing Postgres-backed DuckLake exactly once and configure its S3 data path.
- Bootstrap and validate the Atlas DuckLake schema before reporting ready.
- Bind the local Compose service to the private service network and expose its port on localhost
  for development. TLS is deferred until the production network boundary is known.
- Expose liveness separately from readiness. Readiness requires a successful DuckLake metadata
  query and object-store access, not merely an open Quack port.

The Compose mock uses the local Linux arm64 DuckDB 1.5.4 artifact. The macOS artifact remains
host-test-only. A reproducible production image still requires an immutable published Linux
artifact; do not copy either local development artifact into the repository.

### 2. Establish direct DuckLake catalogue exposure

Status: blocked in Quack 1.5.4.

- Upstream a minimal reproduction using a primary in-memory DuckDB catalogue plus one attached
  Postgres/S3 DuckLake with a unique `atlas` schema.
- Require `remote.atlas.documents` to bind, scan, insert, and transact against that DuckLake table
  with normal bound parameters.
- Require catalogue refresh after server-side DDL without reconnecting.
- Confirm behavior for multiple attached DuckLakes with unique schemas and define rejection of
  duplicate schema names.
- Adopt the released Quack capability and make the platform-assigned schema the Atlas catalogue
  contract.

Until DuckDB 2.0 provides remote catalogue attachment, Atlas uses one explicit temporary transport:
SQL is parsed locally, bound values become typed AST literals, and the resulting statement is sent
through Quack's official `remote.query` escape hatch. This is isolated in
`repository/catalogue/quack.py` and must be deleted at the 2.0 cutover. It does not install views,
aliases, or custom server RPC functions.

### 3. Add the Quack client boundary

Status: temporary 1.5 client implemented and required for application callers.

- Replace local DuckDB configuration in Atlas services with required Quack endpoint, TLS, and
  credential configuration.
- Preserve `execute(sql, parameters)` and result streaming. The temporary binder uses SQLGlot's
  DuckDB AST and an exhaustive typed-literal encoder; raw application text is never interpolated.
- Keep the application-facing `Repository` boundary stable; actions and adapters must not learn
  Quack SQL or catalogue names.
- Use Quack HTTP connection caching and keep logical connections bounded per process.
- Remove local DuckDB thread, memory, temp-directory, S3, and DuckLake catalogue settings from all
  client services once the cutover is complete.

Application processes have no local catalogue fallback. Direct `Catalogue` construction remains
only in deployment/bootstrap and operator commands that run on the stateful boundary, plus isolated
tests.

### 4. Move repository writes across Quack

Status: crawl/document/element microbatch commits implemented and verified through Quack.

- Keep ingestion claiming, raw-object verification, idempotency, and batch construction in the
  repository worker.
- Upload cross-layer staging data to S3 under bounded repository-relative temporary keys.
- Use content-addressed staging keys and verify the uploaded object length before requesting the
  server transaction. DuckDB's Parquet reader validates the staged file while reading it.
- Buffer the reviewed write statements and execute `BEGIN; ...; COMMIT` in one `remote.query`
  request. Separate `remote.query` calls do not share a transaction. Read
  `last_committed_snapshot()` immediately afterward on the same logical Quack connection.
- Preserve the existing rule that the repository worker is the only service credential allowed to
  invoke write operations.
- Delete staging objects only after a confirmed commit or explicit terminal recovery decision.
- Run compaction and other approved maintenance through repository-worker-authorized Quack calls;
  never hide maintenance inside reads.

No worker-local filesystem path may cross the Quack boundary. Development disk storage cannot
model the production cross-layer topology; Compose should use MinIO for this integration path.

### 5. Move CDC access across Quack

Implemented for the experiment. The materialization worker now opens the same Quack-backed
catalogue boundary as the repository worker; CDC is loaded and executed only by the server.
Materialization results cross the process boundary as bounded Parquet staging objects, then the
server performs the target-table and coverage writes in one transaction.

- Load `ducklake_cdc` only on the Quack server.
- Construct `CDCClient` with `install_extension=False` through a typed connection adapter that
  delegates generated CDC SQL to the server.
- Ensure all calls for one open consumer stay on one logical Quack connection so extension lease
  ownership remains stable.
- Retain startup verification of the reviewed CDC build/version.
- Test create, listen/read, heartbeat, commit, release, restart recovery, schema boundary, and
  obsolete-consumer cleanup remotely.
- Remove DuckLake and CDC extension installation from the materialization worker image after
  cutover.

### 6. Authorization and failure behavior

The first local Compose implementation uses one shared token with permissive authorization so the
remote execution path can be developed without premature security machinery. Before production,
introduce separate identities at minimum:

- API/runtime: read-only serving schema.
- Repository worker: reads plus ingestion and maintenance operations.
- Materialization worker: reads plus its CDC and materialization operations.
- Setup/operator: bootstrap and schema administration.

Quack's default authorization is permissive and is not acceptable for this topology. Authorization
must inspect parsed statement type or expose only capability-specific server functions; a prefix
regex is insufficient.

Define explicit behavior for connection loss, server restart, ambiguous commit responses, client
cancellation, query timeout, memory exhaustion, and CDC lease recovery. In particular, an
ambiguous ingestion response must be resolved from DuckLake identity/snapshot state before NATS is
acknowledged or the write is retried.

### 7. Observability and capacity

- Export server query counts, latency, active connections, queued work, memory, temporary storage,
  bytes scanned/read from S3, result bytes, and failures by bounded operation class.
- Correlate Quack connection/query IDs with Atlas run, ingestion, and materialization identifiers.
- Add readiness and alerts for Postgres catalogue, S3, CDC version, memory pressure, and temporary
  storage exhaustion.
- Benchmark cold and warm S3 reads, ingestion throughput, concurrent reads during ingestion,
  compaction interference, large bounded results, and restart recovery.

Centralization creates the opportunity for better cache reuse; it does not guarantee Parquet cache
hits. Production sizing must come from these measurements.

## Cutover sequence

1. Obtain the immutable Linux DuckDB 1.5.4 CDC artifact.
2. Implement and integration-test the Quack service and serving contract against disposable
   Postgres and MinIO state.
3. Convert repository reads, writes, and CDC consumers to Quack clients.
4. Run the full Atlas suite plus ingestion, CDC, compaction, and fault-injection tests.
5. Reset disposable development DuckLake state and start the new topology.
6. Delete all process-local DuckLake attachment code and obsolete client environment variables.

The experiment has completed step 6 for Atlas application processes. Compose clients receive only
the Quack endpoint, logical catalogue/schema names, and object-store access required for raw-object
and bounded staging work; DuckLake metadata and DuckDB resource configuration stay on the Quack
service.

The temporary 1.5 transport is not a permanent compatibility mode. DuckDB 2.0 remote catalogue
attachment replaces it at the cutover boundary, after which the bridge module and environment
switch are deleted.

## Acceptance criteria

- No Atlas API or worker process opens a local DuckDB/DuckLake attachment.
- Only the Quack service has DuckLake Postgres credentials. Workers retain object-store credentials
  for immutable raw objects and bounded cross-layer staging.
- Only repository-worker credentials can request ingestion or maintenance writes.
- All repository values pass through the typed AST binder; no raw string interpolation is used.
- Staging crosses layers only through verified repository-relative S3 objects.
- Ingestion returns the committing transaction's snapshot and remains idempotent after ambiguous
  responses and retries.
- CDC survives Quack and materialization-worker restarts without cursor loss or duplicate durable
  publication.
- Cold/warm and mixed-load benchmarks establish resource limits and show acceptable request and
  ingestion latency.
- Removing any local DuckDB/DuckLake configuration from a client container does not change its
  behavior.

## Current blockers

- The local Linux arm64 DuckDB 1.5.4 `ducklake_cdc` artifact unblocks Compose development. Its
  immutable published counterpart is still required for a reproducible production image.
- `ducklake-client` has no remote Quack connection contract for this split. Prefer a coherent
  upstream addition over duplicating its transaction, result, and schema behavior inside Atlas.
- `ducklake-cdc-client` accepts the tested structural adapter, but that remote/pinned-connection
  contract is not yet documented or tested upstream.
- Quack 1.5.4 discovers schemas from attached DuckLake catalogues but loses the originating
  catalogue/schema when scanning their tables. Direct platform-style `remote.atlas.*` access is
  therefore not functional yet.
