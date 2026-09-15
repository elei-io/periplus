# Operator Common Crawl imports — 2026-09-15

This replaces the initial collection-source experiment. The current contract is
[COMMON_CRAWL.md](../../COMMON_CRAWL.md). Implementation is in the local
`codex/clickhouse-experiment` worktree.

## Result

Collections have one acquisition path again: Periplus crawling. Removed the
Common Crawl request fields, both collection-form selectors, archive frontier
admission/selection, archive navigation generation, and latest-dataset discovery.
The native collection contract rejects the removed `crawler` field.

**Data → Archive imports** launches a separate bounded job with pinned dataset,
explicit capture window, 1–100 URLs, and archive-download allowance. The existing
ingestor process services jobs through one provider-scoped NATS operation lease.
A single new control table holds intent and bounded checkpoints; no service or
queue was added. Capture authority stays in immutable object storage, and ingestion
and materialization continue through the existing pipeline.

Jobs retain diagnostic per-URL results. Missing, unsupported, or retired records
are explicit outcomes. Failures block visibly; retry and cancellation operate on
revision-checked checkpoints. Original timestamps, exact HTML, shared content,
archive replay, and manifest import are retained. Imported links are materialized
without triggering traversal or browser fallback.

## Local evidence

`scripts/common_crawl_smoke.py` passed against real Common Crawl and localhost:

- Job `990a25c0-5183-43c8-8e1d-485e362028d1` imported `https://example.org/` and
  reported a second, absent URL as missing.
- Capture `640b1092-5730-5a9c-a97d-ee8cd0a7c335` preserved capture time
  `2026-08-14T10:16:01Z`. Public SQL returned the `Example Domain` heading.
- Public download returned 559 bytes with SHA-256
  `ff67a9d764d6a2367a187734e697f6a53217db9a21c101d410a113ca871a299d`.
  Its compressed HTML object is 394 bytes and shared with prior equal content.
- Reimport `6e64723d-2cd4-4977-99a7-c21bd1a83226` preserved the capture ID and
  reported the existing archive; no new content identity was invented.
- A one-byte allowance blocked before download; cancellation settled that job.
- Public credentials received HTTP 403 for import controls. Supplying the removed
  collection crawler option received HTTP 422.
- Through the actual browser admin form, job
  `8652baec-3c75-4d85-95d1-b35eddfbd1aa` submitted and completed successfully.
- A fresh native depth-zero, one-page request
  `13ad4e02-5edc-4b79-9c7e-27d2bb93ff78` completed and returned the expected
  heading/link through public SQL (query readiness observed after 17.94 seconds).

Machine-readable results are in the ignored local
`.artifacts/raw-archive-20260915/admin-import-e2e.json`.

Seven targeted tests cover checkpoint recovery with a fresh worker, publication
failure/retry without refetch, missing and unsupported records, retry byte budgets,
cancellation fencing, duplicate capture/body behavior, and immutable job intent.
Existing raw archive integrity/recovery tests continue to pass. The full check
covers 808 backend tests (46 skipped), SDK tests, shared packages, and frontend
checks/builds; admin lint and its 11 tests also pass.

## Deployment and scope

Applied Alembic `20260915_0022` to local control Postgres, backed up control state,
and redeployed existing Compose services. Removed obsolete source options from
six settled disposable local test requests. Immutable historical evidence, raw
objects, existing volumes, production stores, and homelab configuration were not
rewritten. The normal native regression uses the already configured homelab CDP
service only.

This remains a bounded explicit-URL importer, not a bulk corpus-discovery planner.
The public index lookup examines at most 20 candidates. Complete UTF-8 HTTP 200
HTML is the supported WARC subset. Byte allowances cover range attempts, not CDX
traffic or exact storage growth. Completed means archived and published, not query
ready. Full S3-only recovery, corpus deletion, wider WARC formats, and high-throughput
bulk selection remain separate work. The earlier archive process-death/rebuild proof
is recorded in [IMPLEMENTATION.md](IMPLEMENTATION.md); it is not claimed as a new
full rebuild run for this entry-point change.
