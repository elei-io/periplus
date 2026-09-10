# Request retention and reclamation

A request protects the observations that fulfill it. `retention_seconds: null` means
forever and is the default. A positive duration starts at the request's terminal
outcome time, including cancellation or failure. Active and paused requests do not
expire. `expires_at` and `retention_expired` are returned on request details; the
request API exposes the duration and expiry timestamp.
Retention is part of frozen request intent. Changing or extending it requires a
future explicit contract; submitting the same identity with different intent is
not an extension.

Expiry ends protection; it is not a promise of immediate physical erasure. An
expired request's remaining fulfillment rows do not keep results alive. Another
unexpired request, including a private request, does. Missing request definitions
or outcomes protect conservatively. A parent observation ID in a reason is causal
provenance, not a recursive retention root. Autonomous observations without any
request are eligible for eviction after the processing grace period.

## Ownership

- Postgres keeps bounded current work and frozen intent until the existing outbox,
  evidence receipts, selection and interests have settled. Postgres also owns exact
  write claims, retirement tombstones and the resumable object-deletion queue.
  Tombstones contain identities and retirement times, never crawl history.
- DuckLake holds retained results, request definitions, terminal outcomes and
  fulfillment relationships. Evidence remains immutable while retained. The
  retention repository is the sole exception permitted to DELETE result rows.
- The existing Periplus janitor runs bounded logical retirement and deletes
  Periplus-owned raw objects when safe. There is no additional service or queue.
- LakeDucktor alone expires lake snapshots, compacts data and removes obsolete
  registered Parquet. Deleting rows does not itself reclaim those files.

The eviction selector currently supports an explicitly enabled oldest-first sweep.
Automatic storage-capacity measurement, an 80% high-water trigger, paid retention,
renewals and pricing are future policy work. They can select from the same safe
candidate plan. Candidate document sizes are **not** physical bytes reclaimed:
shared content, projections, old snapshots and compression make those different.
Do not use that number as a capacity meter.

## Safe sequence

1. Plan at most one configured batch, ordered by observation finish time and ID.
   Preserve a keyset cursor across janitor sweeps so blocked work does not starve
   later candidates. Enforce a minimum observation age of one hour (one day by
   default), beyond normal publication and traversal completion.
2. Read current Postgres roots in a short transaction. Existing acquisition and
   collection rows block retirement, including pending outbox delivery. Normal
   result reuse requires an existing operational acquisition, so an absent
   acquisition cannot become the target of a newly admitted reuse later.
3. Acquire exact observation/content write claims in control Postgres, then recheck
   request protection in a lake transaction. Record the retirement decision in
   Postgres before deleting evidence and all owned projections in the lake.
   Delete content-owned projections only after the last document reference disappears.
   Include active, rebuilding and retired generations. A crash between these steps
   leaves a durable decision and evidence that the next sweep can finish deleting.
4. Expired collection retirement uses its exact collection claim, deletes fulfillments
   in bounded batches, then its outcome and definition. The retirement tombstone
   prevents delayed ingestion from recreating the collection.
5. Enqueue unreferenced raw keys in Postgres before committing the lake deletion.
   Under the same exact content claims, record a committed snapshot upper bound
   after deletion. Wait until no retained snapshot predates that boundary, then
   start a fresh raw-reader grace period (one day by default, minimum one hour).
   Every new deletion episode resets its snapshot boundary and grace timestamp.
6. Under the content claim and existing NATS lease, create an object-store retirement
   marker, check publication claims and recheck current lake references. Delete raw
   bytes, remove the Postgres queue receipt, then release the marker. Never delete
   registered lake files from Periplus.

Control Postgres owns these tables:

- `lake_write_claims`: composite primary key `(kind, identity)`, owner token and expiry.
  Kinds are observation, content, collection and generation. Completed claims are
  removed; the janitor removes expired claims. No live-identity revision history remains.
- `retired_evidence`: composite primary key `(kind, identity)` and `retired_at`.
  Tombstones prevent delayed jobs from resurrecting retired observations/collections.
  Removing them requires a separately established bounded replay contract.
- `retention_objects`: `object_key`, `content_sha256`, `stored_bytes`, `retired_at`,
  `retired_snapshot`, `snapshots_cleared_at`, and unique `retirement_id`.
  A snapshot of -1 never authorizes deletion. Episode IDs reject stale queue updates.

Claim acquisition uses short Postgres transactions and database time. No Postgres
transaction or advisory lock spans lake I/O. Workers fail-stop after 300 seconds
inside a claim; Periplus connections require PostgreSQL 17+ and bound remote
transactions to 240 seconds. Claims last 600 seconds, covering both bounds plus
60 seconds. Definite rollback releases a claim; ambiguous failures retain it until
expiry. This assumes bounded worker/server execution, not arbitrary process
suspension followed by an unfenced stale write.

Materializers exclude Postgres-retired observations during preparation and recheck
under exact claims before committing. Retirement of prepared input causes fresh
preparation. Insert-only CDC remains the only live cursor; retention deletes its
owned projections directly. Generation claims serialize batch commits and activation;
parsing and Parquet preparation remain parallel.

## Object publication and recovery

Publishers claim `runtime/publications/<content hash>/<observation UUID>` before
uploading or adopting immutable bytes, then check
`runtime/retiring-content/<content hash>`. Reclaimers create the marker before
checking claims. The object store must provide strongly consistent creation,
reads, deletion and prefix listing; the supported local store and deployment S3
backend must meet that contract. A generic eventually consistent S3-compatible
backend is insufficient for destructive reclamation.

The ingestor releases claims after durable commit or confirmed commit replay.
Lost acknowledgements and abandoned producers are reconciled in bounded janitor
metadata batches. Claims are retained for at least the configured dead-letter and
ingestion-result recovery horizons, plus one hour. Current frontier ownership or
unresolved fulfillment evidence blocks orphan retirement. Otherwise an abandoned
observation gets a retirement receipt before its publication claim is released;
a delayed ingestion job cannot recreate it. The raw object still passes through
the ordinary snapshot and reader-grace queue. This is a delivery recovery horizon,
not a shorter retention period for successfully retained observations.

A crashed reclaimer leaves its retirement marker in place. A replacement waits at
least one hour before finishing it. Janitor lake/object operations fail-stop the
process after 300 seconds, and lease loss prevents further marker removal. This
physical guarantee assumes bounded worker execution and strongly consistent object
operations, not arbitrary process suspension followed by an unfenced stale delete.
Keep the janitor singleton as declared by the deployment topology.

## Configuration and review

| Setting | Default | Meaning |
| --- | --- | --- |
| `PERIPLUS_RETENTION_MODE` | `disabled` | `disabled`, `dry_run`, or destructive `purge` |
| `PERIPLUS_RETENTION_BATCH_SIZE` | `25` | At most 100 observation/request candidates per pass |
| `PERIPLUS_RETENTION_MINIMUM_AGE_SECONDS` | `86400` | Processing grace before observation eviction; minimum 3600 |
| `PERIPLUS_RETENTION_OBJECT_GRACE_SECONDS` | `86400` | Reader grace after old snapshots clear; minimum 3600 |

Disabled mode opens no retention catalogue connection and changes no retained
results. Dry-run mode reads candidates and current roots but performs no retention
writes, claim cleanup or raw deletion. Existing transient/operational cleanup still
runs normally. Purge mode explicitly authorizes eviction of eligible unprotected
results; do not enable it expecting an automatic capacity threshold.

For a read-only review from `packages/periplus`, run:

```sh
uv run python -m periplus.retention --limit 25
```

This command always forces dry-run, even when the environment says purge. The
janitor logs candidate/blocked/retired counts and reports `lake_retention` health.
Failures leave resumable receipts; no failure permits skipping a protection check.

The lake contains no Periplus operational tables. Batch completion receipts and
published generation state also live in control Postgres; see [LIFECYCLE.md](LIFECYCLE.md).
Native DuckLake metadata and its CDC cursor remain owned by DuckLake in its metadata
Postgres. Rebuild/retired projection tables contain dataset rows, not operational state.

## Current frontier cleanup cadence

Operational collection/acquisition cleanup retains its one-hour grace, durable
receipt checks, and ownership gates. Each transaction inspects at most 64 records;
collection pruning removes at most 512 dependent rows. A partial collection resumes
at that collection rather than skipping it. Batch results distinguish scan completion
from deletion count, so protected records cannot hide later reclaimable records.

The janitor drains these batches for a code-owned 30-second window, checking shutdown
between transactions. The deadline limits starting new batches; it never interrupts
an in-flight transaction. Other maintenance phases run between windows. An unfinished
scan retries after one second; a completed scan uses `PERIPLUS_JANITOR_INTERVAL_SECONDS`
(default 300 seconds). Failed frontier cleanup uses the normal interval rather than
hot-looping. The `frontier_cleanup` event reports batch count, removed collections and
acquisitions, and whether scanning remains. This cadence does not alter research-data
retention, crawler budgets, or LakeDucktor's physical cleanup policy.

Acquisition cleanup checks unfinished interests rather than the parent request's status.
Once all interests have finished selection/cancellation, navigation is released, and
observation/lineage receipts are committed, janitor clears those interests' execution
references/context and deletes the acquisition/outbox payload. Work shared by multiple
requests remains pinned until every unfinished dependency completes. Compaction touches
at most 512 interests per transaction and resumes partial work before moving on.

Completed interests retain URL deduplication, mode, page-budget state, terminal status
and ingestion confirmation while the request is active. These small records preserve
current progress and final immutable outcome counts without retaining capture payloads.
They disappear when the request is handed off to immutable history. The one-hour grace
also preserves the supported recent-result reuse window; no database-size target drives
cleanup. Stored research evidence and its retention policy are unchanged.

## Contention and failure recovery

Observation and request retirement retry definite transaction rollbacks under fresh
exact claims and a fresh transaction, rechecking protection on each attempt. The
shared five-attempt backoff applies; exhaustion remains a failed sweep. Ambiguous
storage/commit failures are not retried in that call and retain their claims.

Raw reclamation acquires one content lease at a time within the bounded candidate
batch. An active publisher defers only its content; publication-claim cleanup also
skips busy identities. Keyset scans wrap so deferred work is revisited. Duplicate
content shares one lease, and per-content deletion limits sum to the original batch
size. Lease loss and infrastructure errors remain failures, not busy skips.
Structured `retention_sweep` and `retention_reclamation` events record counts
without publishing object keys or observation identities.
