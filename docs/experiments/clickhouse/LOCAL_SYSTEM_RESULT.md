# Local ClickHouse workers and product verification

Verified 2026-09-15 on the MacBook Compose stack. Only browser acquisition uses
homelab CDP; production databases and workers were not changed. Local disposable
volumes were reset before the final acceptance runs.

## Implemented

Actual materializer workers now execute bounded historical ranges independently of
live delivery, resume verified checkpoints, process cancellation and validate
catch-up before explicit atomic publication. Postgres owns operator intent and the
publication pointer; each query binds every public relation to one selected target.
The admin UI exposes creation, pause/resume, cancellation, retry, activation, range
progress and blockers. Storage reports ClickHouse, Postgres and JetStream usage.
Ingestion failures have an operator retry action. Raw downloads verify identity
and length before returning success. Both frontend applications run in Compose.

See [the runbook](../../REBUILDS.md) for ownership, commands and limitations.

## Actual deployed acceptance

- Five real example.com captures were acquired through the configured homelab CDP.
- Rebuild `15ea55cc-a22a-4955-afff-dc283f540bb1` paused historical work while a fresh
  capture became queryable in approximately 16.8 seconds.
- The actual materializer container stopped after one verified historical visit.
  Queries continued; restarting resumed the checkpoint and completed the rebuild.
- Source and target visit IDs and evidence digests matched exactly for all five
  visits. Both contiguous delivery floors reached the persisted barrier (17).
- Activation changed publication revision to 2. Joined public queries and a
  CTE/UNION scoping regression returned all five visits after publication.
- A pre-reset cancellation experiment preserved protection throughout the full
  610-second drain, then reached cancelled and removed its consumer.
- The public browser ran SQL successfully. Its raw-download gateway returned 559
  bytes whose SHA-256 matched the requested content identity.
- A failed-delivery state was injected for an existing real capture. Clicking
  **Retry delivery** in the admin UI restored success, emptied dead letters and
  preserved exactly five unique ingestion rows. The injected failure was removed.
- Admin storage, schema metadata and native SQL endpoints returned successful
  responses through the frontend gateway.

Reproducible deployed coverage is in `scripts/clickhouse_rebuild_smoke.py`.
Local generated evidence lives under ignored `.artifacts/clickhouse/`.

- A rebuild created and activated entirely through the admin buttons
  (`9df3a977-393c-4703-9637-7f43c44cca24`) became serving; public SQL still returned
  all five visits. The admin proxy now refreshes container DNS after backend recreation.
- `make check` passed: 790 backend tests (46 skipped), 24 SDK tests (6 skipped),
  shared-package tests, and both frontend type checks and production builds.
- The opt-in real ClickHouse storage test passed: recovery after content commit,
  batched visit replay, one parse per shared content and exact unique output.

## Scope of this result

This is a working small-corpus local product, not a homelab capacity measurement.
One elected materializer has separate live and history lanes; additional replicas
provide failover, not concurrent historical throughput. Physical retired targets
and query grants remain retained to protect in-flight readers. Automatic physical
reclamation, destructive raw retention, rollback controls, multi-version parser
rollouts, backup/restore drills and sustained representative load remain follow-up
work. Raw object inventory bytes are explicitly unavailable in the storage report.
The broader removal of obsolete DuckLake modules and production deployment
configuration remains outside this local checkpoint.
