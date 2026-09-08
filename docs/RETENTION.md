# Request retention and reclamation

A request protects the observations that fulfill it. `retention_seconds: null` means
forever and is the default. A positive duration starts at the request's terminal
outcome time, including cancellation or failure. Active and paused requests do not
expire. `expires_at` and `retention_expired` are returned on request details; the
public SQL relation `web.collection` exposes the duration and expiry timestamp.
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
  evidence receipts, selection and interests have settled. Retention adds no
  per-observation history or lifetime reference counts to Postgres.
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
3. In one lake transaction, touch the exact observation and content lifecycle
   identities and recheck request protection. Mark the observation retired; delete
   its visit, attempts, steps, document, fulfillments, acquisition reasons and
   visit-owned projections. Delete content-owned projections only after the last
   document reference disappears. Include active, rebuilding and retired registry
   generations, so generation activation cannot restore retired rows.
4. Expired request retirement removes fulfillments in bounded batches, then its
   outcome and definition. Request retirement is independent of physical object
   reclamation. A retired request ID cannot be resubmitted; creation returns 410.
   A deleted request's detail route returns the normal not-found response.
5. Enqueue each unreferenced raw object key in the same transaction. Establish a
   committed snapshot upper bound after deletion. Wait until *all* snapshots at
   or below that boundary have expired, then start a fresh raw-reader grace period
   (one day by default, minimum one hour). LakeDucktor must actually expire
   snapshots for reclamation to advance.
6. Under the existing exact-content NATS operation lease, write an object-store
   retirement marker, check publication claims and recheck lake references. Delete
   raw bytes, remove the queue receipt, then release the marker. Never delete a
   registered lake file from Periplus.

`material._periplus_retention_identities` stores `(kind, identity, revision,
retired_at)`. Ingestion, materialization and retirement update the exact same row
inside their result transaction, so concurrent writers conflict and retry their
checks. Initial creation is serialized by the ingestion operation lease for that
identity. Retired observation/request identities remain as compact replay receipts;
they contain no URL or content. These receipts are an explicit bookkeeping
exception to result retention. Deleting them would allow arbitrarily delayed jobs
to resurrect removed results. Their future compaction needs a bounded replay
contract. Content guards remain usable when the same content hash is observed anew.

`material._periplus_retention_objects` stores `object_key`, `content_sha256`,
`stored_bytes`, `retired_at`, `retired_snapshot`, `snapshots_cleared_at`, and
`retirement_id` (a unique deletion episode preventing stale updates to a later
retirement of the same key). It is a
resumable deletion queue, removed after physical reclamation. A snapshot value of
-1 is a conservative, not-yet-established boundary and never authorizes deletion.

Materializers exclude retired observation IDs even when preparing against an older
pinned snapshot. They touch lifecycle identities before registering files. A
retirement invalidating already prepared files causes fresh preparation, not a
replay of removed evidence. Insert-only live CDC remains the only materialization
cursor; retirement performs its projection deletions directly.

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

Physical contract 6.0.0 requires a coordinated greenfield reset/setup of disposable
5.x state and updated ingestor, materializer, API and janitor processes. There is no
legacy dual-write or migration bridge. Existing 5.x results have no lifecycle
fences and must not be used with these writers. This implementation does not reset
local data, deploy new processes or enable purge automatically.
