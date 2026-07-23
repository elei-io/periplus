# DuckBasin cutover

This is the authoritative implementation ledger for moving Atlas onto DuckBasin. The cutover is
greenfield: replace each old contract and delete it. Do not add local/remote modes, compatibility
environment variables, dual reads or writes, or fallback infrastructure.

## Definition of done

Atlas works end to end while every DuckLake statement executes through a DuckBasin-provided,
session-affine Quack connection.

The local deployment contains only Atlas application processes:

- web and API;
- acquisition workers;
- ingestion workers;
- materialization workers;
- the Basin-CDC-to-Atlas-NATS ingress.

Atlas uses:

- the configured remote `DATABASE_URL` for its editable control plane;
- `ATLAS_NATS_URL` and `ATLAS_NATS_SEED` for all Atlas workflow, KV, queue, and catalogue-event
  state under `atlas.>`;
- `DUCKBASIN_NATS_URL` and `DUCKBASIN_NATS_SEED` only to consume the selected lake's managed CDC
  subjects under `basin.>`;
- the DuckBasin service account, lake discovery API, expiring OAuth tokens, session IDs, and remote
  Quack compute for all DuckLake SQL;
- Atlas's separately configured object repository only for immutable raw HTML, accepted artifacts,
  and bounded navigation packages.

No local Postgres, NATS, Quack, MinIO, DuckLake metadata catalogue, lake Parquet store, CDC
extension, compactor, or lake-maintenance worker remains in the Compose topology.

## Final ownership

DuckBasin owns:

- DuckLake metadata and Parquet storage;
- Quack replicas, horizontal session routing, and compute scaling;
- service-account authentication and connection discovery;
- DuckLake CDC cursors and publication to Basin JetStream;
- compaction, file cleanup, and physical lake maintenance.

Atlas owns:

- crawling, policies, graph execution, and navigation;
- immutable raw capture storage;
- logical crawl/DOM schema and ingestion SQL;
- materialization definitions and refresh semantics;
- Postgres control state;
- Atlas JetStream/KV workflow state;
- the narrow CDC namespace bridge and per-table event fan-out.

## Required cutover

- [x] Make `ATLAS_NATS_*` the only Atlas NATS configuration and add an explicit read-only Basin
  NATS client boundary.
- [x] Make `DuckBasinClientMinter` the only production DuckDB connection factory.
- [x] Replace embedded DuckLake catalogue construction and storage attachment configuration.
- [x] Execute bootstrap, ingestion, repository reads, graph history reads, definitions, interactive
  queries, and materializations through minted Quack connections.
- [x] Use Quack's local Arrow/Parquet transfer for frozen ingestion batches; do not introduce a
  Basin SDK or application lake-storage credentials.
- [x] Replace Atlas's DuckLake CDC consumers with durable Basin JetStream consumers. Fan global
  `table_ids` ticks into stable Atlas `table_uuid` subjects and ACK Basin only after all Atlas
  PubAcks.
- [x] Delete `ducklake-cdc`, `ducklake-cdc-client`, local CDC lease/cursor code, and extension
  validation.
- [x] Delete Atlas lake compaction, old-file cleanup, physical layout maintenance, and their
  catalogue/object-store admission paths. Retain only Atlas-owned raw/navigation retention.
- [x] Remove local Postgres, NATS, Quack, MinIO, and lake volumes from Compose.
- [x] Remove superseded catalogue/storage/Quack environment variables, packages, tests, scripts,
  worker roles, health checks, documentation, and deployment dependencies.
- [x] Reset disposable development state and run setup against the remote Postgres and selected
  Basin lake.
- [x] Prove API and every worker role start with no local infrastructure containers.
- [x] Prove a real crawl stores raw evidence, commits crawl/DOM rows remotely, emits Basin CDC,
  republishes Atlas table ticks, refreshes materializations, and completes graph navigation.

## Accepted upstream hazards

The non-main-schema Quack scan bug and visibility of the server-side `control` catalog through
public remote SQL are recorded in `UPSTREAM.md` and `docs/HAZARDS.md`. They are accepted during the
cutover and remain upstream work; Atlas must not grow permanent workarounds for either.

## Live verification evidence

On 2026-07-23:

- an Atlas KV put/get/purge succeeded with the account-scoped NATS credential;
- the full Compose topology reached healthy with only Atlas API, web, setup, catalogue ingress,
  housekeeping, two acquisition workers, four ingestion workers, and two materialization workers;
- the seeded `single-page` graph acquired `https://example.com/` as run
  `912c7347-047b-48d6-9889-e8728a654562` with one request and no errors;
- remote DuckLake snapshot 95 contained its successful crawl, content-addressed document, and all
  12 expected element rows;
- Atlas API returned the retained raw HTML from the remote object repository; and
- the seeded `same-origin-depth-1` graph completed its outgoing scoped SQL edge with no graph
  errors. Its duplicate-document ingestion exposed a missing production `pytz` dependency,
  entered the dead-letter stream after five retained attempts, and then committed successfully at
  snapshot 96 after the dependency was added and that exact dead letter was requeued; and
- after every backend role was recreated on the corrected image, another duplicate-document crawl
  completed and committed all 12 DOM rows at snapshot 99 with no dead letter or runtime error; and
- after Basin's `atlas` feeds were repaired, `make verify-remote-runtime` advanced both the DDL and
  DML streams with a disposable managed-lake transaction;
- historical CDC replay exposed two Atlas assumptions that were removed: views may share names
  with materialized base tables, and old DML can refer to tables already absent from the current
  DuckLake inventory. The relay drained all 79 DDL and 29 DML source deliveries and republished
  117 stable Atlas catalogue events with no pending source ACKs;
- a Quack session invalidated by Basin scale-to-zero is now reminted transparently before an
  operation. The replacement keeps the configured DuckDB limits and receives a fresh horizontal
  session ID and service-account token; active transactions are never silently moved;
- crawl run `e06f2452-164c-453d-a902-5a73f68e0591` acquired a previously unseen HTTPBin document
  with no graph errors. Remote snapshot 117 contains its crawl, content-addressed raw document, and
  all 6 DOM elements;
- that write advanced the Basin DML stream to sequence 35, the Atlas catalogue event stream to
  sequence 128, and all three fixture materializations to processed snapshot 113 with no pending
  relay ACKs or materialization errors; and
- the new document is present once in `page_metadata`, contributes two `passages`, and correctly
  contributes no `page_links` row because the source page contains no links.

`make verify-remote-runtime` repeats the KV write and disposable managed-lake CDC probe without
printing credentials. `backend/scripts/verify_crawl_run.py <run-id>` waits for and validates a
specific run's remote crawl/DOM evidence.
