# Local archive/corpus validation

This records the earlier, pre-batching local cutover. For the subsequent batched
archive implementation, repeat recovery drills and retained homelab adoption, see
[the current handoff](ARCHIVE_ADOPTION.md). The old local archive is not a
compatibility acceptance case for the new greenfield contract.

Validated on 2026-09-15 on the MacBook Compose deployment. Homelab production
was not changed. The existing homelab CDP endpoint was used for one depth-zero,
one-page native crawl.

## Recovery contract demonstrated

The retained queryable corpus can be reconstructed from raw archive objects and
the matching software, using fresh Postgres, NATS and ClickHouse. Original
business state is not an input. This does not mean those services disappear from
the running product: fresh Postgres still coordinates the rebuild/publication,
and fresh NATS still delivers work.

`scripts/archive_recovery_smoke.py` ran a separate Docker project on an internal
network, without published ports or access to the original databases. Workers
mounted a copied archive read-only. The source comprised eleven existing native
and Common Crawl captures plus 256 deterministic fixtures. One fixture was
retired, leaving 266 retained captures.

| Check | Observed result |
| --- | --- |
| Archive-only bootstrap | 266 captures, exact archived evidence digests, zero duplicate capture identities |
| Original business independence | Zero collections and collection results in fresh Postgres |
| Retirement | Tombstoned fixture remained absent when replaying the earlier manifest |
| Parallel execution and crash | Two materializer processes; one killed during an actual claimed batch; recovery completed 618.62 seconds after the kill |
| Lost notification | A new capture became materialized without publishing any NATS notification |
| Paused historical work | The new live capture appeared while the candidate's historical cursor stayed unchanged |
| Missing/corrupt payload | Missing bytes failed a batch and blocked publication; corrupt bytes failed integrity verification |
| Repair and retry | Restoring exact bytes and requesting retry completed the build, respecting the real write-claim expiry |
| Publication | Candidate activation changed the serving pointer; the selected public capture view contained 267 captures |
| Cancellation and reclamation | Cancelled candidate and older previous target were physically dropped |

For the cancellation/reclamation branches only, **all workers were stopped before
advancing the drain timestamp**. This exercises the physical cleanup and state
transitions; it is not evidence of surviving a full cancellation drain with a
suspended writer. The killed-worker and failed-input retry branches used real
lease clocks. Unit tests separately reject late completion after cancellation.

The final recovery serving build was `750842d9-25bb-4c6a-a4a4-02629e1f0637`.
Generated manifests, logs, copied archive and JSON results are local artifacts,
not committed test fixtures. The primary result files were
`.artifacts/archive-recovery/final-drill/result.json` and `lifecycle-result.json`.

Reproduce with the local Compose configuration available to the process:

```sh
cd packages/periplus
uv run python ../../scripts/archive_recovery_smoke.py \
  --output /absolute/path/to/a/new/recovery-drill
```

The script reads the configured raw repository, copies at most 1,000 events and
uses the locally built `periplus-core:local` image. It writes synthetic captures
only into the copy. Allow roughly 25 minutes for the two real ownership-expiry
checks. Use a new output directory; inspect the script's explicit Docker project
name before running concurrent drills.

## Running local product

- The eleven original capture envelopes and eleven collection result associations
  survived the local cutover. Subsequent collection operations stayed in Postgres.
- A fresh `https://example.com/` crawl through the configured CDP endpoint settled
  and became query-ready in approximately 18 seconds. It consumed one page, had
  no failed pages and added one capture to the serving corpus.
- A new Common Crawl import job resolved the pinned archived example.com capture
  and completed through the current worker. It reused the retained capture;
  this was not a new unique-payload throughput test.
- Admin and public HTTP gateways both returned twelve public captures. The four
  queries in `benchmarks/query/corpus.sql` ran through the Python SDK, including
  captures, elements, links and pages. Parameter binding and SQLAlchemy native
  integer/date/decimal conversion also passed against the actual query service.
  All thirteen landing, dataset and documentation SQL examples executed successfully;
  examples requiring absent fixture pages correctly returned no rows.
- Both gateways downloaded the new raw body; its SHA-256 matched `content_id`.
  A retirement request for that collection-protected capture returned HTTP 409.
- Storage and ingestion status APIs returned the active parts, control tables,
  streams and bounded archive progress. Admin and public applications were built
  and run against this stack.
- Two independently built local targets had identical capture and document output
  digests. The oldest target completed its normal 610-second drain and was
  physically removed. Re-running deployment setup did not recreate its material
  database; public queries still returned twelve captures with zero source lag.

## Automated checks

`make check` passed: Python compilation; 458 backend tests (41 optional tests
skipped); 23 SDK tests (6 skipped); package tests; frontend typechecks, lint and
production builds; chart contract/render checks. Skipped integration tests are
not counted as executed evidence. Two additional tests ran against actual local
Postgres: concurrent claim exclusion/recovery, and retry-aware operational attempt
counting. Admin tests and lint passed.

Setup is tested to preserve published target views and avoid recreating a
reclaimed bootstrap database. Restore setup accepts an exact manifest retry and
rejects a conflicting restore over existing control state.

## Limits still to prove or implement

- This is a correctness/recovery proof, **not a billion-capture throughput or query
  latency result**. No 200 TB capacity claim follows from these fixtures. Measure
  the representative corpus and original business queries next.
- The raw journal currently costs several objects per capture. Packed segments
  may be necessary at large scale. Sixteen shards bound planning state, but do
  not establish sufficient archive write throughput for every workload.
- A material batch contains at most 128 captures, processed sequentially in one
  execution lane per worker. Each HTML payload is bounded to 96 MiB decoded and
  each canonical material row to 127 MiB. The ClickHouse client encodes compact
  UTF-8 JSON once per insert row, batches those exact bytes toward 8 MiB, and
  admits an individual larger row up to a 128 MiB request. Reused-document reads
  allow 256 MiB for ClickHouse's JSON representation. These are separate limits,
  not a 96 MiB total batch-input bound. Larger inputs remain inspectable failures;
  archival retention does not imply that every possible document is queryable.
- Public HTML membership checks and broad element queries still need scaled
  measurements. Reader execution has explicit memory, scan and time limits; a
  query exceeding them fails rather than exhausting the machine.
- Raw retirement is logical. Physical deletion of shared payload bytes, complete
  raw-store capacity accounting and backup lifecycle policy remain future work.
- Common Crawl ingestion supports complete HTTP 200 UTF-8 HTML responses within
  its compressed/expanded byte budgets. It does not support every WARC record
  type or retain original compressed WARC members byte-for-byte.
- Uncertain writes can wait about ten minutes before retry. Process suspension
  beyond the documented drain is outside the ownership contract.
- Parser upgrades need both old and new recipe workers during catch-up. The
  provided Compose/Helm configuration does not automatically orchestrate two
  software releases. Source/lock preservation also needs matching dependency or
  container artifacts for an offline recovery.
- Homelab GitOps deployment and a fresh public crawl/query/raw-download smoke
  passed on 2026-09-15. Full retained-corpus recovery remains unverified: oversized
  materialized rows are explicit failed batches. Full-size recovery time and
  combined-workload capacity remain separate validation work.

## Cleanup boundary

Removed the superseded DuckLake/CDC catalogue runtime, Parquet publication path,
old generation/projection framework, ingestion receipt/DLQ mirrors, abandoned
query rewrites/experiments, stale benchmark runners and their documentation.
Current architecture, schema, lifecycle, rebuild, deployment and retention docs
now describe the implemented path. Migration history remains for Alembic.

The explicitly named `_web_old_dont_touch/` source archive remains untouched and
outside the active package build. Git history retains removed experiments; the
runtime does not keep compatibility routes or dual writes for them. Local backup
artifacts are ignored and are not shipped with the product.

## Homelab restart retry

ClickHouse can return code 394 (query cancelled) during server restart. Wrapped
materialization errors now classify this as retryable, preserving the uncertain-write
lease before replay. Malformed/oversized material output remains a permanent,
inspectable failure. This does not shorten the existing approximately ten-minute
drain interval or bypass ownership checks.

## Retained-document size proof — 2026-09-15

`benchmarks/archive/material_limits.py` exercises projection, real ClickHouse
insertion, full document reuse and verified retry in a disposable database. It
reads immutable raw objects and never changes archive or live material tables.
The selected set contains twelve captures from failed batches and the six largest
unique retained bodies (seventeen unique bodies after overlap).

All seventeen passed in a Kubernetes pod capped at 2 GiB. The largest input was
83,815,376 bytes; the largest encoded document was 84,396,462 bytes. Peak child
RSS across projection/insertion/reuse was 849.4 MiB. Maximum observed projection,
insertion and reuse/retry times were 4.66 s, 0.78 s and 4.89 s respectively. These
are separate maxima and a size-admission proof, not a corpus throughput forecast.
The raw layout, complete DOM representation and deterministic output digests
are unchanged. Source file hashing still produces a new worker recipe, which
must rebuild a candidate alongside the old serving recipe before publication.

A second isolated 2 GiB pod ran all seventeen captures through the actual
`materialize_many` path in one process, then retried the whole batch. It passed
in 52.35 seconds with 1,026.7 MiB peak RSS, including retained Python allocator
memory across documents. Both disposable ClickHouse databases were dropped.
